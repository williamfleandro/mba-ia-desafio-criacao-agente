# Phase 11

Status: PASS

## Implementado
`scripts/validate.py` automatiza os 15 passos do enunciado com API real (uvicorn), Gemini real, restart real do
processo e concorrência real. Também faz as conferências estáticas do passo 15:
- ADK fixado em `pyproject.toml` e `uv.lock`;
- `dados/` idêntico a `be87e1d` e `origin/main`, via `git diff`;
- `.env` não versionado e varredura de chaves;
- pelo menos 2 especialistas;
- root sem o regulamento nas instruções;
- seções e referências do README.

Como o avaliador, o script pode responder perguntas do assistente ("Sim, pode prosseguir...") e registra quantas
vezes isso foi necessário. `tests/fake_app.py` permite validar a mecânica sem chave.

## Arquivos alterados
`scripts/validate.py`, `tests/fake_app.py`, `tests/fake_llm.py`

## Testes executados
`uv run python scripts/validate.py` com o modelo `gemini-3.5-flash`.

## Resultado
Histórico de execuções reais:

| Execução | Resultado | Observação |
|---|---|---|
| 1 | 13 PASS / 2 FAIL | passo 5 com recusa indevida, que derrubou também o 13 |
| 2 | 14 / 1 | passo 12: pingue-pongue de transferências |
| 3 | 10 / 5 | confirmações não retomavam (roteamento do Runner) |
| 4 | 14 / 1 | regulation_agent usou uma pergunta antiga |
| 5, 6, 7 | 15 / 0 | 0 follow-ups |
| 8 | 15 / 0 | 1 follow-up (passo 11), corrigido com a regra "mensagem mais recente" |
| **9, 10, 11 (código final)** | **15 / 0** | **0 follow-ups** |

## Problemas encontrados
A comparação de bytes de `dados/` falhava por causa de `core.autocrlf=true` (working copy em CRLF, blobs em LF).

## Correções realizadas
A comparação passou a usar `git diff --quiet <ref> -- dados/`.

## Evidências
Saída final: `PASS: 15` / `FAIL: 0`.

## Pendências
Nenhuma.
