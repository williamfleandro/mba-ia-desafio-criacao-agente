# Phase 01

Status: PASS

## Implementado
`pyproject.toml` (Python >=3.12, `google-adk==2.11.0`, FastAPI, uvicorn, python-dotenv, aiosqlite;
grupo dev com pytest/httpx; script `aurora-api`), `uv.lock`, `.python-version` (3.12),
`.gitignore` (inclui `.env`, `data/`, `*.db`), `.env.example` (somente nomes), pacote `aurora/`.

## Arquivos alterados
`pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `.env.example`, `aurora/__init__.py`, `aurora/config.py`

## Testes executados
- `uv sync` → `Resolved 57 packages`, sem erro
- `uv run python -c "import google.adk; print(google.adk.__version__)"` → `2.11.0` em Python 3.12.11

## Resultado
Ambiente instalado corretamente.

## Problemas encontrados
Python do sistema é 3.11; resolvido com `.python-version`.

## Correções realizadas
`GOOGLE_GENAI_USE_VERTEXAI` foi removido do `.env.example`: está deprecado no ADK 2.11 e o AI Studio já é o padrão.

## Evidências
`uv.lock`: `name = "google-adk"` / `version = "2.11.0"`; `pyproject.toml`: `google-adk==2.11.0`.

## Pendências
Nenhuma.
