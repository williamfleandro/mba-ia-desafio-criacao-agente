"""Consulta ao regulamento interno (Garantia 4).

O regulamento nunca é carregado em instruções, state ou histórico. Ele é
indexado em memória por capítulo e artigo, e a busca devolve somente os artigos
mais relevantes de UM capítulo — o do assunto perguntado —, de modo que nenhum
evento da sessão recebe trechos de capítulos que tratam de outros assuntos.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from . import config

MAX_ARTICLES = 3
# Artigos com pontuação abaixo desta fração do melhor artigo são descartados.
RELATIVE_CUTOFF = 0.5
# Pontuação mínima do melhor artigo para considerar que o assunto existe.
MIN_SCORE = 1.5

_STOPWORDS = {
    "a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "da", "do", "das", "dos",
    "em", "na", "no", "nas", "nos", "por", "pela", "pelo", "para", "pra", "com", "sem",
    "e", "ou", "que", "qual", "quais", "quando", "quanto", "quantos", "quantas", "como",
    "onde", "ate", "se", "eu", "meu", "minha", "meus", "minhas", "voce", "posso", "pode",
    "podem", "ser", "esta", "estao", "e", "sao", "ao", "aos", "isso", "isto", "sobre",
    "regra", "regras", "regulamento", "condominio", "residencial", "aurora", "existe",
    "tem", "ha", "horas", "hora", "mais", "menos", "muito", "nao", "sim", "la", "aqui",
    "lhe", "me", "te", "nosso", "nossa", "seu", "sua", "dia", "dias", "art", "artigo",
}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _stem(token: str) -> str:
    """Radical simples (prefixo) que aproxima singular/plural e flexões."""
    return token[:6] if len(token) > 6 else token.rstrip("s") or token


# Sinônimos coloquiais -> termos usados no regulamento.
_SYNONYMS = {
    "cachorro": "caes animais", "cachorros": "caes animais", "cao": "caes animais",
    "gato": "animais", "gatos": "animais", "pet": "animais", "pets": "animais",
    "barulho": "silencio ruido", "festa": "festas", "carro": "veiculos garagem",
    "carros": "veiculos garagem", "moto": "veiculos garagem", "vaga": "garagem",
    "lixo": "lixo coleta", "reforma": "obras reformas", "obra": "obras",
    "multa": "multa penalidade infracao", "visita": "visitantes portaria",
    "visitante": "visitantes portaria", "entregas": "entrega portaria",
    "academia": "academia", "mudar": "mudanca", "churrasco": "churrasqueira",
}


def _terms(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", _normalize(text))
    expanded: list[str] = []
    for token in tokens:
        expanded.append(token)
        expanded.extend(_SYNONYMS.get(token, "").split())
    return [_stem(t) for t in expanded if t not in _STOPWORDS and len(t) > 2]


@dataclass(frozen=True)
class Article:
    chapter: str
    text: str
    terms: frozenset[str]


@dataclass(frozen=True)
class Chapter:
    title: str
    title_terms: frozenset[str]
    articles: tuple[Article, ...]


@lru_cache(maxsize=1)
def _index() -> tuple[Chapter, ...]:
    content = config.REGULATION_FILE.read_text(encoding="utf-8")
    chapters: list[Chapter] = []
    for block in re.split(r"(?m)^## ", content)[1:]:
        title, _, body = block.partition("\n")
        title = title.strip()
        parts = re.split(r"(?m)^(?=\*\*Art\. \d+)", body.strip())
        articles = tuple(
            Article(chapter=title, text=p.strip(), terms=frozenset(_terms(p)))
            for p in parts
            if p.strip().startswith("**Art.")
        )
        chapters.append(
            Chapter(title=title, title_terms=frozenset(_terms(title)), articles=articles)
        )
    return tuple(chapters)


def _score(query: list[str], article: Article, chapter: Chapter) -> float:
    score = 0.0
    for term in set(query):
        if term in article.terms:
            score += 1.0
        if term in chapter.title_terms:
            score += 1.5  # o assunto do capítulo pesa mais
    return score


def search(question: str) -> dict:
    """Devolve até ``MAX_ARTICLES`` artigos do capítulo mais relevante."""
    query = _terms(question)
    if not query:
        return {"encontrado": False, "mensagem": "Pergunta sem termos pesquisáveis."}

    best: tuple[float, Chapter, list[tuple[float, Article]]] | None = None
    for chapter in _index():
        scored = sorted(
            ((_score(query, a, chapter), a) for a in chapter.articles),
            key=lambda item: item[0],
            reverse=True,
        )
        top = [item for item in scored[:MAX_ARTICLES] if item[0] > 0]
        if not top:
            continue
        chapter_score = top[0][0] + 0.25 * sum(s for s, _ in top[1:])
        if best is None or chapter_score > best[0]:
            best = (chapter_score, chapter, top)

    if best is None or best[2][0][0] < MIN_SCORE:
        return {
            "encontrado": False,
            "mensagem": "Nenhum trecho do regulamento trata desse assunto.",
        }

    _, chapter, top = best
    cutoff = top[0][0] * RELATIVE_CUTOFF
    articles = [a.text for s, a in top if s >= cutoff]
    return {"encontrado": True, "capitulo": chapter.title, "trechos": articles}
