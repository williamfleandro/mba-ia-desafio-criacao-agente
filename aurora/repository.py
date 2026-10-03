"""Acesso determinístico aos dados do condomínio. Nenhum LLM nesta camada.

Todas as funções que leem ou gravam dados de um apartamento recebem o
apartamento como argumento explícito; quem chama (tools ou rotas de verificação)
é responsável por obtê-lo de fonte confiável — nas tools, a sessão.
"""

from __future__ import annotations

import json
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from . import db


# --------------------------------------------------------------------------- #
# Validação e normalização
# --------------------------------------------------------------------------- #
class DomainError(ValueError):
    """Entrada inválida (área inexistente, data mal formatada...)."""


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().replace("-", " ").replace("_", " ").split())


def parse_date(value: str) -> str:
    """Aceita somente AAAA-MM-DD válido; devolve a data normalizada."""
    value = (value or "").strip()
    try:
        if len(value) != 10:
            raise ValueError
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise DomainError(f"Data inválida: '{value}'. Use o formato AAAA-MM-DD.") from None


@dataclass(frozen=True)
class Area:
    id: str
    name: str
    fee: float


def list_areas() -> list[Area]:
    with db.read() as conn:
        rows = conn.execute("SELECT id, name, fee FROM areas ORDER BY id").fetchall()
    return [Area(r["id"], r["name"], r["fee"]) for r in rows]


def resolve_area(value: str) -> Area:
    """Resolve id ou nome (com ou sem acento) para uma área cadastrada."""
    wanted = _normalize(value)
    areas = list_areas()
    for area in areas:
        if wanted in (_normalize(area.id), _normalize(area.name)):
            return area
    matches = [
        a
        for a in areas
        if wanted
        and (wanted in _normalize(a.name) or _normalize(a.id) in wanted)
    ]
    if len(matches) == 1:
        return matches[0]
    nomes = ", ".join(f"{a.id} ({a.name})" for a in areas)
    raise DomainError(f"Área desconhecida: '{value}'. Áreas disponíveis: {nomes}.")


def apartment_exists(number: str) -> bool:
    with db.read() as conn:
        return (
            conn.execute("SELECT 1 FROM apartments WHERE number = ?", (number,)).fetchone()
            is not None
        )


# --------------------------------------------------------------------------- #
# Sessão -> apartamento (Garantia 2)
# --------------------------------------------------------------------------- #
def bind_session(session_id: str, apartment: str) -> None:
    """Grava o apartamento da sessão. Só a rota POST /sessoes chama isto.

    INSERT puro: uma sessão nunca troca de apartamento (PK impede regravação).
    """
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO session_apartments(session_id, apartment, created_at) VALUES (?, ?, ?)",
            (session_id, apartment, db.now_iso()),
        )


def get_session_apartment(session_id: str) -> str | None:
    with db.read() as conn:
        row = conn.execute(
            "SELECT apartment FROM session_apartments WHERE session_id = ?", (session_id,)
        ).fetchone()
    return row["apartment"] if row else None


# --------------------------------------------------------------------------- #
# Reservas
# --------------------------------------------------------------------------- #
def list_active_reservations(apartment: str) -> list[dict[str, str]]:
    with db.read() as conn:
        rows = conn.execute(
            "SELECT code, area_id, date FROM reservations "
            "WHERE apartment = ? AND status = 'ACTIVE' ORDER BY date, area_id",
            (apartment,),
        ).fetchall()
    return [{"codigo": r["code"], "area": r["area_id"], "data": r["date"]} for r in rows]


def is_slot_available(area_id: str, day: str) -> bool:
    """Livre/ocupado — nunca expõe de quem é a reserva."""
    with db.read() as conn:
        row = conn.execute(
            "SELECT 1 FROM reservations WHERE area_id = ? AND date = ? AND status = 'ACTIVE'",
            (area_id, day),
        ).fetchone()
    return row is None


def _issue_code(conn: sqlite3.Connection) -> str:
    """Gera um código novo e o registra em ``issued_codes`` (PK => nunca repete)."""
    for _ in range(10):
        code = f"RSV-{uuid.uuid4().hex[:10].upper()}"
        try:
            conn.execute(
                "INSERT INTO issued_codes(code, issued_at) VALUES (?, ?)",
                (code, db.now_iso()),
            )
            return code
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("Não foi possível gerar um código de reserva único.")


@dataclass(frozen=True)
class ReservationResult:
    created: bool
    code: str | None = None
    reason: str | None = None  # "ocupada"


def insert_reservation(
    conn: sqlite3.Connection, apartment: str, area_id: str, day: str
) -> ReservationResult:
    """INSERT protegido pelo índice único parcial (Garantia 5).

    Não há SELECT prévio decidindo nada: quem decide é o banco no momento da
    gravação. Conflito vira resultado de negócio ("ocupada"), não exceção.
    Usa SAVEPOINT para que um conflito não desfaça o restante da transação.
    """
    conn.execute("SAVEPOINT reserve")
    try:
        code = _issue_code(conn)
        conn.execute(
            "INSERT INTO reservations(code, apartment, area_id, date, status, created_at) "
            "VALUES (?, ?, ?, ?, 'ACTIVE', ?)",
            (code, apartment, area_id, day, db.now_iso()),
        )
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK TO reserve")
        conn.execute("RELEASE reserve")
        return ReservationResult(created=False, reason="ocupada")
    conn.execute("RELEASE reserve")
    return ReservationResult(created=True, code=code)


def create_reservation(apartment: str, area_id: str, day: str) -> ReservationResult:
    with db.transaction() as conn:
        return insert_reservation(conn, apartment, area_id, day)


def cancel_reservation(apartment: str, area_id: str, day: str) -> str | None:
    """Cancela a reserva ATIVA do próprio apartamento. Devolve o código ou None.

    O filtro ``apartment = ?`` está no próprio UPDATE: reserva de outro
    apartamento simplesmente não é encontrada (e nada é revelado sobre ela).
    """
    with db.transaction() as conn:
        row = conn.execute(
            "UPDATE reservations SET status = 'CANCELLED', cancelled_at = ? "
            "WHERE apartment = ? AND area_id = ? AND date = ? AND status = 'ACTIVE' "
            "RETURNING code",
            (db.now_iso(), apartment, area_id, day),
        ).fetchone()
    return row["code"] if row else None


# --------------------------------------------------------------------------- #
# Visitantes
# --------------------------------------------------------------------------- #
def list_visitors(apartment: str) -> list[dict[str, str]]:
    with db.read() as conn:
        rows = conn.execute(
            "SELECT name, date FROM visitors WHERE apartment = ? ORDER BY date, id",
            (apartment,),
        ).fetchall()
    return [{"nome": r["name"], "data": r["date"]} for r in rows]


def insert_visitor(conn: sqlite3.Connection, apartment: str, name: str, day: str) -> None:
    conn.execute(
        "INSERT INTO visitors(apartment, name, date, created_at) VALUES (?, ?, ?, ?)",
        (apartment, name, day, db.now_iso()),
    )


# --------------------------------------------------------------------------- #
# Confirmações (Garantia 1)
# --------------------------------------------------------------------------- #
PENDING, APPROVED, DENIED = "PENDING", "APPROVED", "DENIED"


@dataclass(frozen=True)
class Confirmation:
    id: str
    session_id: str
    apartment: str
    function_call_id: str
    action: str
    details: dict[str, Any]
    status: str
    executed_at: str | None
    result: dict[str, Any] | None


def _row_to_confirmation(row: sqlite3.Row) -> Confirmation:
    return Confirmation(
        id=row["id"],
        session_id=row["session_id"],
        apartment=row["apartment"],
        function_call_id=row["function_call_id"],
        action=row["action"],
        details=json.loads(row["details"]),
        status=row["status"],
        executed_at=row["executed_at"],
        result=json.loads(row["result"]) if row["result"] else None,
    )


def create_confirmation(
    session_id: str,
    apartment: str,
    function_call_id: str,
    action: str,
    details: dict[str, Any],
) -> Confirmation:
    """Registra uma confirmação PENDING (idempotente por chamada de tool)."""
    with db.transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO confirmations"
            "(id, session_id, apartment, function_call_id, action, details, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?)",
            (
                str(uuid.uuid4()),
                session_id,
                apartment,
                function_call_id,
                action,
                json.dumps(details, ensure_ascii=False),
                db.now_iso(),
            ),
        )
        row = conn.execute(
            "SELECT * FROM confirmations WHERE session_id = ? AND function_call_id = ?",
            (session_id, function_call_id),
        ).fetchone()
    return _row_to_confirmation(row)


def get_confirmation_for_call(session_id: str, function_call_id: str) -> Confirmation | None:
    with db.read() as conn:
        row = conn.execute(
            "SELECT * FROM confirmations WHERE session_id = ? AND function_call_id = ?",
            (session_id, function_call_id),
        ).fetchone()
    return _row_to_confirmation(row) if row else None


def pending_confirmations(session_id: str) -> list[Confirmation]:
    with db.read() as conn:
        rows = conn.execute(
            "SELECT * FROM confirmations WHERE session_id = ? AND status = 'PENDING' "
            "ORDER BY created_at",
            (session_id,),
        ).fetchall()
    return [_row_to_confirmation(r) for r in rows]


def respond_confirmation(
    confirmation_id: str, session_id: str, approved: bool
) -> Confirmation | None:
    """Transição atômica PENDING -> APPROVED/DENIED.

    O ``WHERE`` exige id + sessão + status PENDING. Id inexistente, de outra
    sessão ou já respondido => nenhuma linha afetada => None (a API devolve 409).
    """
    with db.transaction() as conn:
        row = conn.execute(
            "UPDATE confirmations SET status = ?, responded_at = ? "
            "WHERE id = ? AND session_id = ? AND status = 'PENDING' RETURNING *",
            (APPROVED if approved else DENIED, db.now_iso(), confirmation_id, session_id),
        ).fetchone()
    return _row_to_confirmation(row) if row else None


def execute_confirmed(
    confirmation_id: str,
    effect: Callable[[sqlite3.Connection, Confirmation], dict[str, Any]],
) -> dict[str, Any] | None:
    """Executa o efeito de uma confirmação APROVADA no máximo uma vez.

    Marca ``executed_at`` e aplica o efeito na MESMA transação: ou ambos
    acontecem, ou nenhum. Se já executada / não aprovada, devolve None.
    """
    with db.transaction() as conn:
        row = conn.execute(
            "UPDATE confirmations SET executed_at = ? "
            "WHERE id = ? AND status = 'APPROVED' AND executed_at IS NULL RETURNING *",
            (db.now_iso(), confirmation_id),
        ).fetchone()
        if row is None:
            return None
        confirmation = _row_to_confirmation(row)
        result = effect(conn, confirmation)
        conn.execute(
            "UPDATE confirmations SET result = ? WHERE id = ?",
            (json.dumps(result, ensure_ascii=False), confirmation_id),
        )
    return result
