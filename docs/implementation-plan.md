# Plano de implementação — Assistente Virtual Residencial Aurora

## Ambiente auditado (Fase 0)

| Item | Valor |
|---|---|
| Branch | `develop` (main = `main`) |
| Git status inicial | limpo (`be87e1d chore: enunciado e dados do desafio`) |
| Python do sistema | 3.11.15 (insuficiente) → projeto usa **CPython 3.12.11** gerenciado pelo uv (`.python-version`) |
| uv | 0.11.19 |
| google-adk | **2.11.0** (última da série 2 no PyPI em 2026-10-03; fixada com `==`) |
| Arquivos originais | `README.md` (enunciado), `.gitignore`, `dados/*.json`, `dados/regulamento.md` |

SHA-256 dos dados originais (devem permanecer idênticos):

```
42ee4ac2b8216997af66141b4a5965662d9add1a778dde8d40d3d31272b4ce4c  dados/apartamentos.json
060909790630e097b9c0a7eb4f0aee01954aee9fc9b35c8c15c052f8f74ddfc3  dados/areas.json
b1c92405812d91ff8253689fe228c64b7c8bf31b8e59b1fff6f320f9214456be  dados/regulamento.md
e1a75d8adcc5692099a5b2fe6be47c3809b193b7aecfcf0475a88df797688541  dados/reservas.json
1adb8988048fe0a920d83153054414f6d3c431d073ec0986d352d0c8bfd2134c  dados/visitantes.json
```

## Leitura do código-fonte do ADK 2.11.0 (decisões que dependem dele)

* `ToolContext.request_confirmation(hint, payload)` (`google/adk/agents/context.py`) registra
  `actions.requested_tool_confirmations[function_call_id]`; o fluxo emite uma chamada
  `adk_request_confirmation` com `originalFunctionCall` e pausa (`skip_summarization`).
* O cliente responde com `FunctionResponse(name="adk_request_confirmation", id=<id da chamada de confirmação>, response={"confirmed": bool})`
  (`flows/llm_flows/tools/_confirmation.py`). O processador valida nome/argumentos contra o
  histórico e **só processa se o agente corrente for o autor** da chamada original.
* `agents/_agent_router.find_agent_to_run`: com `ResumabilityConfig(is_resumable=True)`, uma
  resposta de função é roteada para o autor da chamada correspondente → o especialista que pediu
  a confirmação. Sem resumability, cai na lógica de “último agente transferível” — fonte do
  bug silencioso citado no enunciado. **Decisão: App com `is_resumable=True`.**
* `sessions/sqlite_session_service.SqliteSessionService` (aiosqlite) → sessões/eventos persistentes.

## Arquitetura

```
FastAPI (aurora/api.py)
   └─ AssistantService (aurora/service.py) ── Runner(App(root_agent, resumable)) ── SqliteSessionService (data/sessions.db)
        └─ root_agent ──transfer──> reservation_agent | visitor_agent | regulation_agent
                                         │                 │                 │
                                   tools (aurora/tools.py) — identidade via sessão
                                         │
                       repository (aurora/repository.py) ── SQLite (data/aurora.db)
```

* Identidade: tabela `session_apartments` (gravada só por `POST /sessoes`) + `state["apartamento"]`.
  Tools leem `tool_context.session.id` → apartamento do banco; nenhuma tool recebe apartamento.
* Confirmações: tabela `confirmations` (id UUID, session_id, function_call_id, acao, detalhes,
  status PENDING/APPROVED/DENIED, executed_at). Resposta via `UPDATE ... WHERE status='PENDING'`
  atômico (rowcount 0 → 409). Execução “no máximo uma vez” via `executed_at IS NULL` na mesma
  transação do INSERT.
* Concorrência: índice único parcial `reservations(area_id, date) WHERE status='ACTIVE'`;
  `IntegrityError` → resposta normal “data ocupada”.
* Códigos: `RSV-<10 hex de uuid4>` registrados em `issued_codes` (PK) que **não** é apagada pelo
  restore e é semeada com os códigos originais → nunca há reutilização.
* Regulamento: parser em capítulos/artigos; busca por palavras-chave; retorna apenas artigos do
  capítulo mais relevante.
* Restore: `uv run python -m scripts.restore` apaga reservas/visitantes/confirmações e recarrega
  `dados/`. Sessões **são preservadas** por padrão (`--sessions` apaga também).

## Fases

0 auditoria · 1 bootstrap · 2 persistência · 3 repository · 4 tools · 5 multi-agent ·
6 confirmações · 7 API · 8 regulamento · 9 restart · 10 concorrência · 11 validador · 12 docs.

Testes determinísticos usam um LLM falso (`tests/fake_llm.py`, subclasse de `BaseLlm`) sobre
sessão SQLite real; a validação final (`scripts/validate.py`) usa Gemini real.
