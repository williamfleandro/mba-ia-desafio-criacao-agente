# Phase 05

Status: PASS

## Implementado
`aurora/agents.py::build_root_agent` monta o `root_agent` (roteador, sem tools de dados, sem regulamento) com os
`sub_agents` `reservation_agent`, `visitor_agent` e `regulation_agent`. Os especialistas usam
`disallow_transfer_to_parent/peers=True`; o `regulation_agent` usa `include_contents="none"`.

## Arquivos alterados
`aurora/agents.py`

## Testes executados
`tests/test_regulation_and_agents.py::test_agent_tree_and_root_without_regulation`,
`tests/test_api_flow.py::test_routing_is_explicit`.

## Resultado
Transferências root → especialista verificadas nos eventos. Nenhuma instrução contém texto do regulamento.

## Problemas encontrados (com Gemini real)
1. Depois de "Sou do 302", o especialista passou a recusar até o cancelamento da própria reserva (passo 5 FAIL).
2. Transferências em "pingue-pongue" entre especialistas e root, terminando com resposta vazia (passo 12 FAIL).
3. O `regulation_agent` buscou a primeira pergunta da sessão em vez da atual (passo 12 FAIL).
4. Um especialista respondeu a uma mensagem antiga em vez da atual (passo 11 exigiu follow-up).

## Correções realizadas
1. Regras de identidade: o morador é sempre o apartamento da sessão; o agente não recusa por desconfiança, porque as tools já limitam o escopo.
2. Especialistas não transferem; cada mensagem nova volta ao root (roteamento explícito, ver fase 06).
3. `include_contents="none"` no `regulation_agent`.
4. Regra "atenda somente a mensagem mais recente".

## Evidências
Execuções finais do validador (9, 10 e 11): 15/15 PASS, 0 follow-ups.

## Pendências
Nenhuma.
