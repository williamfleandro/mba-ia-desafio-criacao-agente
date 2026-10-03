"""LLM determinístico para testes offline do fluxo ADK real.

Não substitui o Gemini na validação final; serve para exercitar com precisão
transferência, tools, pausa por confirmação e retomada em sessão SQLite.
O "modelo" é propositalmente ingênuo: obedece ao que o usuário escreve — inclusive
"sou do 302" —, provando que a segurança vem do código e não do prompt.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import AsyncGenerator

from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_OWN_TOOLS = {
    "list_my_reservations", "check_area_availability", "create_reservation",
    "cancel_my_reservation", "list_my_visitors", "authorize_visitor", "search_regulation",
}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _area(text: str) -> str:
    t = _norm(text)
    if "salao" in t:
        return "salao-de-festas"
    if "churras" in t:
        return "churrasqueira"
    return "quadra"


def _call(name: str, args: dict) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]
        )
    )


def _text(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


class FakeLlm(BaseLlm):
    model: str = "fake-llm"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield self._decide(llm_request)

    def _decide(self, req: LlmRequest) -> LlmResponse:
        system = str(req.config.system_instruction or "") if req.config else ""
        agent = (
            "reservation_agent" if "especialista em reservas" in system
            else "visitor_agent" if "especialista em visitantes" in system
            else "regulation_agent" if "especialista no regulamento" in system
            else "root_agent"
        )
        # Último item relevante: resposta de tool própria ou texto do usuário.
        for content in reversed(req.contents or []):
            parts = content.parts or []
            if parts and parts[0].text and parts[0].text.startswith("For context"):
                continue
            for part in parts:
                fr = part.function_response
                if fr and fr.name in _OWN_TOOLS:
                    return _text(f"[{agent}] resultado: {json.dumps(fr.response, ensure_ascii=False)}")
                if (
                    content.role == "user" and part.text
                    and not part.text.startswith("For context")
                ):
                    return self._act(agent, part.text)
        return _text("Como posso ajudar?")

    def _act(self, agent: str, text: str) -> LlmResponse:
        t = _norm(text)
        dates = DATE.findall(text)
        domains = {
            "regulation_agent": "piscina" in t or "regulamento" in t or "horas" in t,
            "visitor_agent": "visit" in t or "libera" in t or "entrada" in t,
            "reservation_agent": "reserv" in t or "cancel" in t,
        }
        if agent == "root_agent":
            target = next((name for name, hit in domains.items() if hit), None)
            if target:
                return _call("transfer_to_agent", {"agent_name": target})
            return _text("Posso ajudar com reservas, visitantes e regulamento.")
        if not domains[agent]:  # especialistas não transferem
            return _text("Esse assunto pode ser tratado na próxima mensagem.")
        if agent == "reservation_agent":
            if "cancel" in t and dates:
                return _call("cancel_my_reservation", {"area": _area(text), "data": dates[0]})
            if "reserve" in t and dates:
                return _call("create_reservation", {"area": _area(text), "data": dates[0]})
            return _call("list_my_reservations", {})
        if agent == "visitor_agent":
            m = re.search(r"entrada d[aoe]s? ([A-ZÀ-Ú][\wÀ-ú]+(?: [A-ZÀ-Ú][\wÀ-ú]+)*)", text)
            if m and dates:
                return _call("authorize_visitor", {"nome": m.group(1), "data": dates[0]})
            return _call("list_my_visitors", {})
        return _call("search_regulation", {"pergunta": text})
