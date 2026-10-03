"""Camada determinística: dados iniciais, exclusividade no banco, códigos, restore."""

from __future__ import annotations

import inspect
from concurrent.futures import ThreadPoolExecutor

import pytest

from aurora import db, repository, tools


def test_seed_data(data_dir):
    assert repository.list_active_reservations("101") == [
        {"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}
    ]
    assert repository.list_visitors("302") == [{"nome": "Marina Duarte", "data": "2030-03-16"}]


def test_unique_index_blocks_second_active_reservation(data_dir):
    first = repository.create_reservation("101", "churrasqueira", "2030-07-01")
    second = repository.create_reservation("201", "churrasqueira", "2030-07-01")
    assert first.created and not second.created and second.reason == "ocupada"


def test_concurrent_inserts_only_one_wins(data_dir):
    """Muitas threads (conexões independentes) disputando a mesma área/data."""
    apartments = ["101", "102", "201", "202", "301", "302"] * 5

    def attempt(apt: str):
        return repository.create_reservation(apt, "salao-de-festas", "2030-08-08")

    with ThreadPoolExecutor(max_workers=len(apartments)) as pool:
        results = list(pool.map(attempt, apartments))
    assert sum(r.created for r in results) == 1
    with db.read() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM reservations WHERE area_id='salao-de-festas' "
            "AND date='2030-08-08' AND status='ACTIVE'"
        ).fetchone()[0]
    assert n == 1


def test_codes_never_reused_even_after_cancel_and_restore(data_dir):
    codes = set()
    for _ in range(3):
        r = repository.create_reservation("101", "quadra", "2030-09-09")
        assert r.created and r.code not in codes
        codes.add(r.code)
        assert repository.cancel_reservation("101", "quadra", "2030-09-09") == r.code
    db.restore_seed_data()
    with db.read() as conn:
        issued = {row[0] for row in conn.execute("SELECT code FROM issued_codes")}
    assert codes <= issued  # restore não libera códigos já emitidos
    assert {"RSV-1377", "RSV-4821", "RSV-2950"} <= issued


def test_cancel_other_apartment_is_not_found(data_dir):
    assert repository.cancel_reservation("101", "salao-de-festas", "2030-03-16") is None
    assert repository.list_active_reservations("302")[0]["codigo"] == "RSV-4821"


def test_confirmation_transitions_are_atomic(data_dir):
    conf = repository.create_confirmation("s1", "101", "fc-1", "x", {"a": 1})
    assert repository.respond_confirmation(conf.id, "outra-sessao", True) is None
    assert repository.respond_confirmation("inexistente", "s1", True) is None
    assert repository.respond_confirmation(conf.id, "s1", True).status == "APPROVED"
    assert repository.respond_confirmation(conf.id, "s1", True) is None  # replay
    assert repository.respond_confirmation(conf.id, "s1", False) is None
    calls = []
    effect = lambda conn, c: calls.append(c.id) or {"ok": True}
    assert repository.execute_confirmed(conf.id, effect) == {"ok": True}
    assert repository.execute_confirmed(conf.id, effect) is None  # no máximo uma vez
    assert calls == [conf.id]


def test_invalid_inputs(data_dir):
    with pytest.raises(repository.DomainError):
        repository.parse_date("20/04/2030")
    with pytest.raises(repository.DomainError):
        repository.resolve_area("piscina")
    assert repository.resolve_area("Salão de Festas").id == "salao-de-festas"


def test_no_tool_accepts_an_apartment_argument():
    for fn in (
        tools.list_my_reservations, tools.check_area_availability, tools.create_reservation,
        tools.cancel_my_reservation, tools.list_my_visitors, tools.authorize_visitor,
        tools.search_regulation,
    ):
        params = {p.lower() for p in inspect.signature(fn).parameters}
        assert not any("apart" in p or "apto" in p for p in params), fn.__name__
