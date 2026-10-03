# Phase 02

Status: PASS

## Implementado
`aurora/db.py`: schema SQLite com as tabelas `apartments`, `areas`, `issued_codes`, `reservations` (com o índice
único parcial `ux_reservations_active_slot`), `visitors`, `session_apartments` e `confirmations`. Usa WAL,
`busy_timeout`, transações `BEGIN IMMEDIATE`, `restore_seed_data` e `ensure_initialized`.
`scripts/restore.py` roda com `uv run python -m scripts.restore [--sessions]`.
Sessões ADK: `SqliteSessionService` em `data/sessions.db`.

Decisão de restore: apaga reservas, visitantes e confirmações e **preserva as sessões** por padrão
(`--sessions` apaga também). `issued_codes` nunca é apagado, então os códigos não se repetem após um restore.

## Arquivos alterados
`aurora/db.py`, `scripts/__init__.py`, `scripts/restore.py`

## Testes executados
`uv run python -m scripts.restore` seguido de leitura via repository; `tests/test_repository.py::test_seed_data`.

## Resultado
101 → `[{"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}]`;
302 → `[{"nome": "Marina Duarte", "data": "2030-03-16"}]`.

## Problemas encontrados
- A saída do console Windows usava cp1252.
- `AURORA_DATA_DIR` relativo dependia do diretório corrente.

## Correções realizadas
- `sys.stdout.reconfigure(encoding="utf-8")` no script de restore.
- Caminho relativo agora é resolvido a partir da raiz do projeto (`config.data_dir`).

## Evidências
`sha256sum dados/*` idêntico antes e depois; `git diff --quiet be87e1d -- dados/` OK.

## Pendências
Nenhuma.
