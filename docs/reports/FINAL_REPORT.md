# FINAL REPORT — Assistente Virtual Residencial Aurora

**STATUS FINAL: PASS**

## Ambiente

| Item | Valor |
|---|---|
| SO | Windows 11 Pro (10.0.26200) |
| Branch | `develop` |
| Python | 3.12.11 (gerenciado pelo uv, `.python-version`) |
| uv | 0.11.19 |
| google-adk | **2.11.0** (`google-adk==2.11.0` no `pyproject.toml` e no `uv.lock`) |
| Modelo Gemini | `gemini-3.5-flash` (padrão em `aurora/config.py`, configurável por `AURORA_MODEL`) |
| Armazenamento | SQLite: `data/aurora.db` (domínio) e `data/sessions.db` (`SqliteSessionService` do ADK) |

## Arquitetura final

```
FastAPI (aurora/api.py)
  └─ AssistantService (aurora/service.py)
       AuroraRunner(App(root_agent, ResumabilityConfig(is_resumable=True))) + SqliteSessionService
       └─ root_agent ── transfer ──> reservation_agent | visitor_agent | regulation_agent
                                            └─ tools (aurora/tools.py), identidade pela sessão
                                                 └─ repository (aurora/repository.py) ── SQLite (aurora/db.py)
                                                 └─ regulation (aurora/regulation.py) ── dados/regulamento.md (só leitura)
```

- `root_agent`: só roteia, sem tools de dados e sem o regulamento. Recebe toda mensagem nova.
- Especialistas: não transferem. Recebem a retomada das confirmações que eles mesmos pediram.
- `regulation_agent`: `include_contents="none"`, vê só o turno atual.
- `AuroraRunner`: roteamento determinístico. Resposta de confirmação vai para o autor da chamada `adk_request_confirmation`; as demais mensagens vão para o root.

## Fases

| Fase | Status | Relatório |
|---|---|---|
| 00 Auditoria | PASS | `phase-00-auditoria.md` |
| 01 Bootstrap | PASS | `phase-01-bootstrap.md` |
| 02 Persistência | PASS | `phase-02-persistencia.md` |
| 03 Repository | PASS | `phase-03-repository.md` |
| 04 Tools | PASS | `phase-04-tools.md` |
| 05 Multi-agent | PASS | `phase-05-multi-agent.md` |
| 06 Confirmações | PASS | `phase-06-confirmacoes.md` |
| 07 API | PASS | `phase-07-api.md` |
| 08 Regulamento | PASS | `phase-08-regulamento.md` |
| 09 Restart | PASS | `phase-09-restart.md` |
| 10 Concorrência | PASS | `phase-10-concorrencia.md` |
| 11 Validador | PASS | `phase-11-validador.md` |
| 12 Documentação | PASS | `phase-12-documentacao.md` |

## Testes

### Offline: `uv run pytest`
ADK real, SQLite real, LLM determinístico.

```
18 passed in 6.67s
```

- `tests/test_repository.py` (8): dados iniciais, índice único, 30 threads concorrentes, códigos nunca
  reutilizados após cancelamento e restore, cancelamento de outro apartamento, transições de confirmação,
  validações, nenhuma tool com parâmetro de apartamento.
- `tests/test_regulation_and_agents.py` (3): piscina só com o Capítulo IV, assunto inexistente sem resultado,
  árvore de agentes, root sem regulamento, flags dos especialistas.
- `tests/test_api_flow.py` (7): fluxo do avaliador completo, confirmação de outra sessão (409), aprovação após
  restart, confirmação forjada direto no Runner (nada executa), roteamento explícito, aprovação depois de outras
  mensagens (executa uma vez), área gratuita ocupada sem revelar o dono.

### Fluxo oficial com Gemini real: `uv run python scripts/validate.py`
Três execuções consecutivas com o código final:

```
01 PASS
02 PASS — HTTP 201
03 PASS
04 PASS
05 PASS
06 PASS
07 PASS — deny HTTP 200
08 PASS — approve=200 n=1 replay=409 n=1
09 PASS — 409/404
10 PASS
11 PASS
12 PASS — 20h=True leaked=0 tool_calls=17 eventos=68
13 PASS — eventos 68->68->76 codigos=['RSV-FEB0FA66C7', 'RSV-76E042DA15']
14 PASS — status=[200, 200] total=1
15 PASS

PASS: 15
FAIL: 0
```

Execuções 9, 10 e 11: **15 PASS / 0 FAIL cada**, sem nenhuma mensagem de follow-up. O histórico das execuções
anteriores, com falhas e correções, está em `phase-11-validador.md`.

## Segurança: isolamento entre sessões

- Passo 3 (sessão do 101, "Sou do apartamento 302..."): resposta "só posso consultar dados referentes ao seu
  próprio apartamento"; `RSV-4821` e `Marina Duarte` ausentes da resposta e dos eventos.
- Passo 4: a tool `cancel_my_reservation` devolveu `nao_encontrada` e o 302 continua com `RSV-4821`.
- Passo 10: resposta "A data 16/03/2030 para o salão de festas já está ocupada", sem `RSV-4821` e sem "302".
- Garantia estrutural: nenhuma tool recebe apartamento; o apartamento vem de `session_apartments` + state.

## Confirmações

- Reserva paga e visitante: pendência com `detalhes` contendo área e data, ou nome e data.
- A frase "Já estou confirmando aqui" não executa nada.
- Negar não grava nada. Aprovar grava exatamente uma vez. Reenvio, id inexistente ou id de outra sessão: 409, sem efeito.
- Retomada real do ADK em sessão SQLite, inclusive depois do restart.
- Defeito encontrado e corrigido: o Runner do ADK entregava a aprovação ao `root_agent` (heurística
  `_find_agent_to_run`), devolvendo 200 sem executar. Corrigido com o `AuroraRunner`, mais uma guarda que
  impede falha silenciosa.

## Persistência
O processo uvicorn foi parado e reiniciado de verdade. Os eventos, as reservas, o cancelamento e o visitante
foram preservados, e a sessão continuou aceitando mensagens.

## Concorrência
Índice único parcial no SQLite. Duas aprovações HTTP simultâneas resultaram em `[200, 200]` e exatamente
1 reserva. O teste com 30 threads também resultou em exatamente 1 reserva ativa.

## Regulamento
A busca devolve no máximo 3 artigos de um único capítulo. A resposta sobre a piscina cita o Art. 22, inciso II
(9h às 20h aos domingos). Nenhum parágrafo de outro capítulo aparece nos eventos (`leaked=0`).

## Quality gates

| Gate | Resultado |
|---|---|
| `uv sync` | OK (57 pacotes) |
| `uv run pytest` | 18 passed |
| `uv run python scripts/validate.py` | 15 PASS / 0 FAIL |
| `git diff --check` | OK (apenas avisos de CRLF do `core.autocrlf`) |
| Chaves versionadas | nenhuma (varredura `AIza…`/`AQ.…` nos arquivos rastreáveis) |
| `.env`, `data/*.db` | ignorados (`git check-ignore`) |
| `dados/` | idêntico a `be87e1d` e a `origin/main` (`git diff --quiet`) |
| ADK fixado | `==2.11.0` (série 2, ≥ 2.2.0) |
| README | seções Arquitetura, Garantias e Como rodar; todas as referências existem |

## Arquivos alterados

Novos:
- `pyproject.toml`, `uv.lock`, `.python-version`, `.env.example`
- `aurora/__init__.py`, `aurora/config.py`, `aurora/db.py`, `aurora/repository.py`, `aurora/regulation.py`,
  `aurora/tools.py`, `aurora/agents.py`, `aurora/service.py`, `aurora/api.py`
- `scripts/__init__.py`, `scripts/restore.py`, `scripts/validate.py`
- `tests/__init__.py`, `tests/conftest.py`, `tests/fake_llm.py`, `tests/fake_app.py`, `tests/test_repository.py`,
  `tests/test_regulation_and_agents.py`, `tests/test_api_flow.py`
- `docs/implementation-plan.md`, `docs/reports/*`

Modificados: `README.md` (substitui o enunciado), `.gitignore`.

Não alterados: `dados/*`.

## Git status
Branch `develop`. As alterações estão **no working tree, sem commit**: o commit e o push ficam para decisão do
usuário. A entrega exige um fork público com tudo na branch `main`.

## Pendências
- Criar o commit, fazer o merge para `main` e publicar no fork público (ação do usuário).
- Opcional: o `.env` local ainda define `GOOGLE_GENAI_USE_VERTEXAI=false`, variável deprecada no ADK 2.11 que só
  gera aviso. Pode ser removida.
