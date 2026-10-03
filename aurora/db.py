"""SQLite do condomínio: conexão, schema e carga dos dados originais.

As garantias que dependem do armazenamento vivem aqui, no schema, e não no prompt:

* ``ux_reservations_active_slot`` — índice único parcial: no máximo UMA reserva
  ATIVA por (área, data). Vale no instante do INSERT, inclusive sob concorrência
  (Garantia 5).
* ``issued_codes`` — registro permanente de todo código já emitido (inclusive os
  do arquivo original e os de reservas canceladas). Não é apagado pelo restore,
  então um código nunca se repete (regra de negócio 5).
* ``confirmations`` — confirmações com ``status`` e ``executed_at``; a resposta e a
  execução são transições atômicas (Garantia 1).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS apartments (
    number   TEXT PRIMARY KEY,
    resident TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS areas (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    fee  REAL NOT NULL CHECK (fee >= 0)
);

CREATE TABLE IF NOT EXISTS issued_codes (
    code      TEXT PRIMARY KEY,
    issued_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reservations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT NOT NULL UNIQUE REFERENCES issued_codes(code),
    apartment    TEXT NOT NULL,
    area_id      TEXT NOT NULL REFERENCES areas(id),
    date         TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('ACTIVE', 'CANCELLED')),
    created_at   TEXT NOT NULL,
    cancelled_at TEXT
);

-- Garantia 5: exclusividade de (área, data) entre reservas ativas, no banco.
CREATE UNIQUE INDEX IF NOT EXISTS ux_reservations_active_slot
    ON reservations(area_id, date) WHERE status = 'ACTIVE';

CREATE INDEX IF NOT EXISTS ix_reservations_apartment
    ON reservations(apartment, status);

CREATE TABLE IF NOT EXISTS visitors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    apartment  TEXT NOT NULL,
    name       TEXT NOT NULL,
    date       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_visitors_apartment ON visitors(apartment);

-- Garantia 2: o apartamento de cada sessão é gravado uma única vez pela API.
CREATE TABLE IF NOT EXISTS session_apartments (
    session_id TEXT PRIMARY KEY,
    apartment  TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Garantia 1: confirmações externas à conversa.
CREATE TABLE IF NOT EXISTS confirmations (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    apartment        TEXT NOT NULL,
    function_call_id TEXT NOT NULL,
    action           TEXT NOT NULL,
    details          TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('PENDING', 'APPROVED', 'DENIED')),
    created_at       TEXT NOT NULL,
    responded_at     TEXT,
    executed_at      TEXT,
    result           TEXT,
    UNIQUE (session_id, function_call_id)
);

CREATE INDEX IF NOT EXISTS ix_confirmations_session
    ON confirmations(session_id, status);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Abre uma conexão própria (uma por operação; seguro entre threads).

    ``isolation_level=None`` deixa o controle de transação explícito
    (``BEGIN IMMEDIATE`` nas escritas), e ``timeout`` faz escritores
    concorrentes esperarem o lock em vez de falhar.
    """
    conn = sqlite3.connect(
        path or config.domain_db_path(), timeout=30, isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def read() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Transação de escrita: ``BEGIN IMMEDIATE`` obtém o lock de escrita já no início."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    finally:
        conn.close()


def init_schema() -> None:
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
    finally:
        conn.close()


def _load_json(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def restore_seed_data(*, clear_sessions: bool = False) -> dict[str, int]:
    """Volta reservas e visitantes ao estado de ``dados/`` (arquivos só são lidos).

    * Apaga reservas, visitantes e confirmações (estado operacional).
    * Recarrega apartamentos, áreas, reservas e visitantes dos arquivos originais.
    * ``issued_codes`` NÃO é apagado: códigos emitidos antes continuam bloqueados.
    * Sessões só são apagadas com ``clear_sessions=True``.
    """
    init_schema()
    apartments = _load_json(config.APARTMENTS_FILE)
    areas = _load_json(config.AREAS_FILE)
    reservations = _load_json(config.RESERVATIONS_FILE)
    visitors = _load_json(config.VISITORS_FILE)
    ts = now_iso()
    with transaction() as conn:
        conn.execute("DELETE FROM confirmations")
        conn.execute("DELETE FROM reservations")
        conn.execute("DELETE FROM visitors")
        for apt in apartments:
            conn.execute(
                "INSERT INTO apartments(number, resident) VALUES (?, ?) "
                "ON CONFLICT(number) DO UPDATE SET resident = excluded.resident",
                (apt["numero"], apt["morador"]),
            )
        for area in areas:
            conn.execute(
                "INSERT INTO areas(id, name, fee) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, fee = excluded.fee",
                (area["id"], area["nome"], float(area["taxa"])),
            )
        for rsv in reservations:
            conn.execute(
                "INSERT OR IGNORE INTO issued_codes(code, issued_at) VALUES (?, ?)",
                (rsv["codigo"], ts),
            )
            conn.execute(
                "INSERT INTO reservations(code, apartment, area_id, date, status, created_at) "
                "VALUES (?, ?, ?, ?, 'ACTIVE', ?)",
                (rsv["codigo"], rsv["apartamento"], rsv["area"], rsv["data"], ts),
            )
        for vis in visitors:
            conn.execute(
                "INSERT INTO visitors(apartment, name, date, created_at) VALUES (?, ?, ?, ?)",
                (vis["apartamento"], vis["nome"], vis["data"], ts),
            )
        if clear_sessions:
            conn.execute("DELETE FROM session_apartments")
    if clear_sessions:
        for suffix in ("", "-wal", "-shm"):
            Path(str(config.sessions_db_path()) + suffix).unlink(missing_ok=True)
    return {
        "apartamentos": len(apartments),
        "areas": len(areas),
        "reservas": len(reservations),
        "visitantes": len(visitors),
    }


def ensure_initialized() -> bool:
    """Cria o schema e, se o banco estiver vazio, carrega os dados originais.

    Nunca sobrescreve um banco já populado (reiniciar a API preserva os dados).
    Retorna True se carregou os dados originais.
    """
    init_schema()
    with read() as conn:
        populated = conn.execute("SELECT COUNT(*) FROM apartments").fetchone()[0] > 0
    if populated:
        return False
    restore_seed_data()
    return True
