# Assistente Virtual — Residencial Aurora

Assistente do aplicativo dos moradores do Residencial Aurora, construído com
**Google ADK 2.11.0** e **Gemini**, exposto por uma API **FastAPI** em
`http://localhost:8000`. O morador reserva áreas comuns, cancela as próprias
reservas, autoriza visitantes e tira dúvidas do regulamento — sem que nenhuma
mensagem consiga furar as regras do condomínio.

> **O modelo decide o caminho; o código decide o que é permitido.**

O enunciado original do desafio está preservado no histórico do Git (commit `be87e1d`).

---

# Arquitetura

```
 HTTP ──> FastAPI (aurora/api.py)
            │
            ▼
        AssistantService (aurora/service.py)
            │  AuroraRunner(App(root_agent, ResumabilityConfig(is_resumable=True)))
            │  SqliteSessionService  ──> data/sessions.db   (sessões, state, eventos)
            ▼
        root_agent ──transfer_to_agent──┬─> reservation_agent ─┐
                                        ├─> visitor_agent ─────┤ tools (aurora/tools.py)
                                        └─> regulation_agent ──┘     │
                                                                     ▼
                                          repository (aurora/repository.py) ──> data/aurora.db
                                          regulation (aurora/regulation.py) ──> dados/regulamento.md (só leitura)
```

## Agentes (`aurora/agents.py`, função `build_root_agent`)

| Agente | Responsabilidade | Tools | Como é acionado |
|---|---|---|---|
| `root_agent` | Entende a intenção e encaminha. Não acessa dados e **não recebe o regulamento** nas instruções. | nenhuma (só `transfer_to_agent`, automática do ADK) | Recebe **toda** mensagem nova do morador. |
| `reservation_agent` | Consultar reservas do apartamento, verificar disponibilidade, criar e cancelar reservas. | `list_my_reservations`, `check_area_availability`, `create_reservation`, `cancel_my_reservation` | Transferência a partir do root (`sub_agents`). Recebe também a retomada das confirmações que pediu. |
| `visitor_agent` | Consultar visitantes e autorizar a entrada de visitantes. | `list_my_visitors`, `authorize_visitor` | Transferência a partir do root. Recebe também a retomada das confirmações que pediu. |
| `regulation_agent` | Somente dúvidas sobre o regulamento, respondidas com os trechos devolvidos pela busca. Vê só o turno atual (`include_contents="none"`). | `search_regulation` | Transferência a partir do root. |

**Por que transferência (`sub_agents`) e não `AgentTool`?** A confirmação de
tools do ADK pausa a invocação e precisa ser retomada **no mesmo agente que a
pediu**, dentro da sessão persistida. Um `AgentTool` executa o especialista numa
sessão interna em memória, e a pendência se perderia no reinício. Com
transferência, a chamada pausada fica nos eventos da sessão SQLite com o
especialista como autor, e a resposta da confirmação é entregue a ele.

**Roteamento explícito (`aurora/service.py`, classe `AuroraRunner`).** O `Runner` padrão do
ADK escolhe quem atende com uma heurística (`_find_agent_to_run`): olha o último evento
persistido e, se ele não for uma resposta de função, usa o "último agente transferível".
Nos testes com Gemini e sessão SQLite, essa escolha variava com a ordem dos eventos gravados
e com os bloqueios de transferência. Com especialistas que não transferem, a aprovação
chegava ao `root_agent`, que não é o autor da confirmação: a rota devolvia 200 e a ação não
executava. O `AuroraRunner` torna a escolha determinística:

* resposta de confirmação → o agente autor da chamada `adk_request_confirmation`, lido dos
  eventos persistidos (`_find_confirmation_request`);
* qualquer outra mensagem → `root_agent`.

Os especialistas têm `disallow_transfer_to_parent/peers=True`. Eles fazem o trabalho do turno
e devolvem o controle, e o próximo pedido passa de novo pelo root. Isso eliminou o
"pingue-pongue" de transferências observado com o modelo real. Se uma retomada aprovada não
executar a tool, `AssistantService.respond_confirmation` registra erro e avisa na resposta:
a falha nunca é silenciosa.

**Por que três especialistas?** Cada um tem um conjunto pequeno e coeso de
tools, o que reduz chamadas erradas. O regulamento fica isolado num agente cujo
contexto só recebe trechos pontuais, e os dados de reservas e de visitantes
nunca se misturam com o texto do regulamento.

## Tools (`aurora/tools.py`)

Nenhuma tool recebe "apartamento" como argumento: o apartamento vem sempre de
`session_apartment(tool_context)`. Todas devolvem dicionários JSON e nunca
informam de quem é uma reserva que não pertence ao morador.

## Persistência

* `data/aurora.db` (SQLite, `aurora/db.py`) guarda apartamentos, áreas, reservas,
  visitantes, confirmações, o vínculo sessão→apartamento e o registro de códigos emitidos.
* `data/sessions.db` (`SqliteSessionService` do ADK) guarda sessões, state e eventos.
* Na primeira subida, se o banco estiver vazio, os dados de `dados/` são carregados
  (`db.ensure_initialized`). Reiniciar a API não apaga nada.
* Os arquivos de `dados/` são apenas lidos e nunca são alterados.

## API (`aurora/api.py`)

| Método e rota | Resposta |
|---|---|
| `POST /sessoes` `{"apartamento": "101"}` | `201 {"session_id": "..."}` |
| `POST /sessoes/{id}/mensagens` `{"texto": "..."}` | `200 {"resposta", "confirmacoes_pendentes": [{"id","acao","detalhes"}]}` |
| `POST /sessoes/{id}/confirmacoes` `{"id","confirmado"}` | `200` (mesmo formato) ou `409` |
| `GET /sessoes/{id}/eventos` | `200` lista completa e ordenada de eventos (`Event.model_dump`) ou `404` |
| `GET /apartamentos/{apto}/reservas` | `200 [{"codigo","area","data"}]` (reservas ativas, lidas direto do banco) |
| `GET /apartamentos/{apto}/visitantes` | `200 [{"nome","data"}]` (lidos direto do banco) |

Rotas com `{session_id}` devolvem `404` para sessão inexistente. `POST /sessoes`
com apartamento fora de `dados/apartamentos.json` devolve `422`.

---

# Garantias

## Garantia 1 — cobrança ou acesso só com confirmação

**Onde:**
* `aurora/tools.py`: `create_reservation` (área com `fee > 0`), `authorize_visitor` (sempre),
  `_request_confirmation` e `_resume_confirmed`.
* `aurora/repository.py`: `create_confirmation`, `respond_confirmation`, `execute_confirmed`.
* `aurora/service.py`: `AssistantService.respond_confirmation` e `_find_confirmation_request`.
* `aurora/db.py`: tabela `confirmations`.

**Como funciona:**
1. A tool calcula os detalhes em código (área validada, taxa, data AAAA-MM-DD), grava uma
   confirmação `PENDING` no banco e chama `tool_context.request_confirmation(...)`. O ADK
   emite `adk_request_confirmation` e pausa. Nada é gravado em reservas ou visitantes.
2. `confirmacoes_pendentes` é lido do banco (`repository.pending_confirmations`).
3. `POST /confirmacoes` executa `UPDATE ... SET status=? WHERE id=? AND session_id=? AND
   status='PENDING' RETURNING *`. Se o id não existe, é de outra sessão ou já foi respondido,
   nenhuma linha muda e a API devolve **409** sem executar nada.
4. O serviço localiza no histórico persistido a chamada `adk_request_confirmation` da tool e
   envia ao Runner um `FunctionResponse(name="adk_request_confirmation", response={"confirmed": ...})`.
   O `AuroraRunner` entrega a resposta ao especialista autor da chamada, que reexecuta a tool
   com `tool_context.tool_confirmation` preenchido (App com `ResumabilityConfig(is_resumable=True)`).
5. Na retomada, `_resume_confirmed` **ignora o booleano vindo do ADK** e consulta o status no
   banco. Só `APPROVED` executa, via `execute_confirmed`, que marca `executed_at IS NULL → agora`
   e grava o efeito na **mesma transação**. Assim a ação executa no máximo uma vez.

**Por que não depende do LLM:** a tool pede a confirmação em código, sem perguntar ao modelo.
A frase "já estou confirmando aqui" é só texto: nenhuma tool lê a conversa para decidir.
Mesmo uma resposta de confirmação forjada e injetada direto no Runner não executa nada,
porque o status no banco continua `PENDING`
(`tests/test_api_flow.py::test_forged_confirmation_without_api_executes_nothing`).

## Garantia 2 — cada sessão pertence a um apartamento

**Onde:**
* `aurora/service.py`: `AssistantService.create_session` grava `state["apartamento"]` e
  chama `repository.bind_session` (tabela `session_apartments`, PK por sessão, só INSERT).
* `aurora/tools.py`: `session_apartment(tool_context)` lê o apartamento da sessão em
  `session_apartments` e confere com `tool_context.state["apartamento"]`.
* `aurora/repository.py`: `list_active_reservations`, `cancel_reservation` e `list_visitors`
  filtram por `apartment = ?` no próprio SQL. `is_slot_available` devolve apenas um booleano.
* `aurora/tools.py`: `check_area_availability` devolve só `disponivel` e `situacao`
  (livre/ocupada), nunca o dono nem o código da reserva.

**Por que não depende do LLM:** nenhuma tool tem parâmetro de apartamento
(`tests/test_repository.py::test_no_tool_accepts_an_apartment_argument`). O apartamento é
definido uma vez na criação da sessão pela API e o modelo não tem como alterá-lo. "Sou do 302"
não muda nada: o `UPDATE` de cancelamento tem `WHERE apartment = <sessão>`, então uma reserva
do 302 simplesmente não é encontrada e nenhum dado dela entra nos eventos.

## Garantia 3 — nada se perde no reinício

**Onde:**
* `aurora/service.py`: `AssistantService.__init__` usa `SqliteSessionService(data/sessions.db)`.
* `aurora/db.py`: `ensure_initialized` cria o schema e só carrega `dados/` se o banco estiver vazio.
* Confirmações pendentes ficam na tabela `confirmations` e a chamada pausada fica nos eventos.
  Por isso uma aprovação feita **depois** do reinício também retoma a tool
  (`tests/test_api_flow.py::test_approval_after_restart_resumes`).

**Por que não depende do LLM:** é armazenamento em disco. O modelo não guarda estado.

## Garantia 4 — o regulamento é consultado, não carregado

**Onde:**
* `aurora/regulation.py`: `_index` divide `dados/regulamento.md` em capítulos e artigos, e
  `search` pontua os artigos por termos (com sinônimos coloquiais), escolhe **um único
  capítulo** e devolve no máximo 3 artigos dele, acima de um limiar relativo.
* `aurora/tools.py`: `search_regulation`. `aurora/agents.py`: só o `regulation_agent` tem
  essa tool, e nenhum agente tem o regulamento nas instruções.

**Por que não depende do LLM:** o recorte é feito em código. Seja qual for a pergunta que o
modelo envie, a tool nunca devolve artigos de dois capítulos, então nenhum evento recebe
trechos de outros assuntos. O texto do regulamento não aparece em `ROOT_INSTRUCTION`
(`tests/test_regulation_and_agents.py`).

## Garantia 5 — dois moradores, uma reserva

**Onde:**
* `aurora/db.py`: `CREATE UNIQUE INDEX ux_reservations_active_slot ON reservations(area_id, date)
  WHERE status = 'ACTIVE'`.
* `aurora/repository.py`: `insert_reservation` faz o INSERT dentro de `SAVEPOINT`. Um
  `sqlite3.IntegrityError` vira o resultado de negócio `ocupada`, não uma exceção, e a
  transação é aberta com `BEGIN IMMEDIATE` (`db.transaction`).
* Códigos: `repository._issue_code` gera `RSV-<10 hex de uuid4>` e o registra em
  `issued_codes` (PK). Essa tabela não é apagada pelo restore e já contém os códigos
  originais, então um código nunca se repete, nem de reserva cancelada.

**Por que não depende do LLM:** não existe "conferir e depois gravar" decidindo nada. A
exclusividade vale no instante do INSERT, garantida pelo banco. Na disputa, a segunda
aprovação recebe `200` com "data ocupada"
(`tests/test_repository.py::test_concurrent_inserts_only_one_wins` com 30 threads, e o passo 14
de `scripts/validate.py` com duas requisições HTTP simultâneas).

---

# Como rodar

## Pré-requisitos

* [uv](https://docs.astral.sh/uv/) (ele instala o Python 3.12 automaticamente, conforme `.python-version`).
* Uma chave da Gemini API do [Google AI Studio](https://aistudio.google.com/apikey).
* Nenhum serviço externo: o armazenamento é SQLite em `data/`.

## Configuração

```bash
cp .env.example .env
# edite .env e preencha GOOGLE_API_KEY
uv sync
```

Variáveis do `.env`:

| Variável | Obrigatória | Descrição |
|---|---|---|
| `GOOGLE_API_KEY` | sim | Chave do Google AI Studio |
| `AURORA_MODEL` | não | Modelo Gemini dos agentes (padrão `gemini-3.5-flash`, em `aurora/config.py`) |
| `AURORA_DATA_DIR` | não | Pasta dos bancos SQLite, relativa à raiz do projeto (padrão `data`) |

## Restaurar os dados iniciais

```bash
uv run python -m scripts.restore
```

Apaga reservas, visitantes e confirmações e recarrega `dados/` (os arquivos só são lidos).
**As sessões são preservadas.** Para apagar também sessões e eventos, rode com a API parada:
`uv run python -m scripts.restore --sessions`. Códigos de reserva já emitidos continuam
bloqueados mesmo após o restore.

## Subir a API

```bash
uv run aurora-api
```

A API responde em `http://localhost:8000` (equivalente:
`uv run uvicorn aurora.api:app --port 8000`). Use um único processo/worker: as execuções
de uma mesma sessão são serializadas em memória. Pare com Ctrl+C e suba de novo com o mesmo
comando: sessões, eventos e dados continuam.

## Exemplo

```bash
curl -s -X POST localhost:8000/sessoes -H "Content-Type: application/json" -d '{"apartamento":"101"}'
# {"session_id":"<S1>"}

curl -s -X POST localhost:8000/sessoes/<S1>/mensagens -H "Content-Type: application/json" \
  -d '{"texto":"Reserve o salão de festas para 2030-04-20."}'
# {"resposta":"...","confirmacoes_pendentes":[{"id":"<C1>","acao":"reservar_area",
#   "detalhes":{"area":"salao-de-festas","nome_area":"Salão de festas","data":"2030-04-20","taxa":150.0}}]}

curl -s -X POST localhost:8000/sessoes/<S1>/confirmacoes -H "Content-Type: application/json" \
  -d '{"id":"<C1>","confirmado":true}'

curl -s localhost:8000/apartamentos/101/reservas
curl -s localhost:8000/sessoes/<S1>/eventos
```

## Testes e validação

```bash
uv run pytest                      # testes offline: ADK real + SQLite + LLM determinístico
uv run python scripts/validate.py  # fluxo oficial de 15 passos com Gemini real
```

`scripts/validate.py` restaura os dados (incluindo sessões), sobe a API na porta 8000,
executa os passos 1 a 12, para e sobe a API de novo (passo 13), dispara as duas aprovações
simultâneas (passo 14) e faz as conferências do repositório (passo 15). Para validar só a
mecânica sem chave: `uv run python scripts/validate.py --app tests.fake_app:app`.
Rode o validador com a porta 8000 livre.

Relatórios de implementação e validação: `docs/reports/`.
