"""Configuração central: caminhos dos dados e variáveis de ambiente."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Arquivos originais do condomínio (somente leitura).
SEED_DIR = PROJECT_ROOT / "dados"
APARTMENTS_FILE = SEED_DIR / "apartamentos.json"
AREAS_FILE = SEED_DIR / "areas.json"
RESERVATIONS_FILE = SEED_DIR / "reservas.json"
VISITORS_FILE = SEED_DIR / "visitantes.json"
REGULATION_FILE = SEED_DIR / "regulamento.md"

APP_NAME = "residencial_aurora"
DEFAULT_MODEL = "gemini-3.5-flash"


def data_dir() -> Path:
    path = Path(os.getenv("AURORA_DATA_DIR") or "data")
    if not path.is_absolute():
        path = PROJECT_ROOT / path  # relativo à raiz do projeto, não ao diretório corrente
    path.mkdir(parents=True, exist_ok=True)
    return path


def domain_db_path() -> Path:
    """Banco do condomínio: reservas, visitantes, confirmações, códigos."""
    return data_dir() / "aurora.db"


def sessions_db_path() -> Path:
    """Banco de sessões/eventos do ADK (SqliteSessionService)."""
    return data_dir() / "sessions.db"


def model_name() -> str:
    return os.getenv("AURORA_MODEL") or DEFAULT_MODEL
