# Phase 06

Status: PASS

## Implementado
- A tool registra `PENDING` no banco e chama `tool_context.request_confirmation`; o ADK emite `adk_request_confirmation` e pausa.
- `POST /confirmacoes`:
  1. faz a transição atômica no banco;
  2. busca a chamada `adk_request_confirmation` nos eventos persistidos;
  3. envia um `FunctionResponse` ao Runner;
  4. a tool é reexecutada com `tool_confirmation`;
  5. `_resume_confirmed` só executa se o banco diz `APPROVED`, e no máximo uma vez (`execute_confirmed`).
- `AuroraRunner` (`aurora/service.py`) faz o roteamento explícito: resposta de confirmação vai para o agente
  autor; as demais mensagens vão para o root.
- Uma aprovação que não executa é registrada como erro e informada na resposta, nunca silenciosamente.

## Arquivos alterados
`aurora/tools.py`, `aurora/repository.py`, `aurora/service.py`

## Testes executados
- `test_evaluator_flow`: deny, approve, replay 409, id inexistente 409.
- `test_confirmation_of_other_session_is_409`
- `test_approval_after_restart_resumes`
- `test_forged_confirmation_without_api_executes_nothing`
- `test_approval_after_other_messages_still_executes_once`
- `test_routing_is_explicit`

## Resultado
Todos verificados: criação, pending, deny, approve, replay → 409, id inexistente → 409, id de outra sessão → 409,
e retomada real do ADK em sessão SQLite, inclusive após restart.

## Problemas encontrados
Com Gemini real, as aprovações devolviam 200 mas nada executava (passos 8, 11 e 14 FAIL, `total=0`).
Causa, lida em `google/adk/runners.py` e `agents/_agent_router.py`:
- o Runner escolhe o agente com `_find_agent_to_run(session)` antes de anexar a mensagem nova;
- o último evento gravado era a chamada `adk_request_confirmation`, não uma resposta de função;
- o fallback "último agente transferível" escolhia o root, que ignora confirmações de outro autor.

## Correções realizadas
`AuroraRunner._find_agent_to_run` determinístico, via `ContextVar` com o autor lido dos eventos persistidos.

## Evidências
Validador real: `08 PASS — approve=200 n=1 replay=409 n=1`; `14 PASS — status=[200, 200] total=1`.

## Pendências
Nenhuma.
