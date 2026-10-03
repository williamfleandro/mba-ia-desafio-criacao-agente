from __future__ import annotations

from aurora import config, regulation
from aurora.agents import build_root_agent
from tests.fake_llm import FakeLlm


def _chapters():
    content = config.REGULATION_FILE.read_text(encoding="utf-8")
    return {b.split("\n", 1)[0].strip(): b for b in content.split("\n## ")[1:]}


def test_pool_sunday_only_pool_chapter():
    result = regulation.search("Até que horas a piscina funciona aos domingos?")
    assert result["capitulo"] == "Capítulo IV: Piscina"
    joined = "\n".join(result["trechos"])
    assert "Aos domingos e feriados, a piscina funciona das 9h às 20h" in joined
    pool = _chapters()["Capítulo IV: Piscina"]
    for trecho in result["trechos"]:
        assert trecho in pool  # todo trecho é do capítulo da piscina


def test_unknown_subject_returns_nothing():
    assert regulation.search("Qual a cor do céu?")["encontrado"] is False


def test_agent_tree_and_root_without_regulation():
    root = build_root_agent(FakeLlm())
    assert root.name == "root_agent"
    assert {a.name for a in root.sub_agents} == {
        "reservation_agent", "visitor_agent", "regulation_agent"
    }
    assert root.tools == []
    regulation_agent = next(a for a in root.sub_agents if a.name == "regulation_agent")
    assert regulation_agent.include_contents == "none"
    assert all(a.disallow_transfer_to_parent and a.disallow_transfer_to_peers for a in root.sub_agents)
    regulation_text = config.REGULATION_FILE.read_text(encoding="utf-8")
    articles = [p for p in regulation_text.split("\n\n") if len(p) > 40]
    for agent in [root, *root.sub_agents]:
        for article in articles:
            assert article[:40] not in agent.instruction
