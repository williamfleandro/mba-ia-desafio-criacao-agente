"""Orquestração ADK: App + Runner + sessão persistida em SQLite.

Também implementa a ponte entre a rota HTTP de confirmações e o mecanismo de
confirmação de tools do ADK (Garantia 1):

1. ``repository.respond_confirmation`` faz a transição atômica PENDING ->
   APPROVED/DENIED (falhou => 409, nada executa).
2. Localizamos no histórico persistido a chamada ``adk_request_confirmation``
   cujo ``originalFunctionCall.id`` é o da tool pausada.
3. Enviamos ao Runner um ``FunctionResponse`` para essa chamada, e o
   :class:`AuroraRunner` entrega a execução ao agente AUTOR da chamada (o
   especialista), que reexecuta a tool com ``tool_context.tool_confirmation``
   preenchido.

Por que um Runner próprio: o ``Runner`` do ADK escolhe o agente com
``_find_agent_to_run(session)`` ANTES de anexar a mensagem nova, olhando o
último evento persistido e, na falta de correspondência, o "último agente
transferível". Essa heurística muda conforme a ordem dos eventos gravados e os
bloqueios de transferência — com Gemini, a aprovação chegava ao ``root_agent``,
que não é o autor da confirmação, e a execução terminava sem efeito e sem erro.
Aqui a escolha é determinística e feita em código.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from dataclasses import dataclass
from typing import Any

from google.adk.apps import App, ResumabilityConfig
from google.adk.events import Event
from google.adk.models import BaseLlm
from google.adk.models.google_llm import Gemini
from google.adk.runners import Runner
from google.adk.agents import BaseAgent
from google.adk.sessions import Session
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types

from . import config, db, repository
from .agents import build_root_agent

logger = logging.getLogger("aurora.service")

REQUEST_CONFIRMATION = "adk_request_confirmation"


# Agente que deve receber a execução corrente (None => root_agent).
_TARGET_AGENT: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "aurora_target_agent", default=None
)


class AuroraRunner(Runner):
    """Runner com roteamento explícito.

    * Resposta de confirmação -> agente autor de ``adk_request_confirmation``.
    * Qualquer outra mensagem -> ``root_agent``, que escolhe o especialista.
    """

    def _find_agent_to_run(self, session: Session, root_agent: BaseAgent) -> BaseAgent:
        target = _TARGET_AGENT.get()
        if target:
            agent = root_agent.find_agent(target)
            if agent is None:
                raise RuntimeError(f"Agente {target!r} não existe na árvore")
            return agent
        return root_agent


class SessionNotFound(LookupError):
    pass


class ConfirmationConflict(LookupError):
    pass


@dataclass
class TurnResult:
    resposta: str
    confirmacoes_pendentes: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {"resposta": self.resposta, "confirmacoes_pendentes": self.confirmacoes_pendentes}


def _user_id(apartment: str) -> str:
    return f"apartamento-{apartment}"


def default_model() -> BaseLlm:
    # Repetição com backoff para erros transitórios (429/5xx) da Gemini API.
    return Gemini(
        model=config.model_name(),
        retry_options=types.HttpRetryOptions(
            attempts=6, initial_delay=2, max_delay=30, exp_base=2
        ),
    )


class AssistantService:
    def __init__(self, model: str | BaseLlm | None = None) -> None:
        db.ensure_initialized()
        self.session_service = SqliteSessionService(str(config.sessions_db_path()))
        self.app = App(
            name=config.APP_NAME,
            root_agent=build_root_agent(model or default_model()),
            resumability_config=ResumabilityConfig(is_resumable=True),
        )
        self.runner = AuroraRunner(app=self.app, session_service=self.session_service)
        # Uma execução por sessão por vez (sessões diferentes rodam em paralelo).
        self._locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        await self.runner.close()

    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    # ------------------------------------------------------------------ #
    # Sessões
    # ------------------------------------------------------------------ #
    async def create_session(self, apartment: str) -> str:
        session = await self.session_service.create_session(
            app_name=config.APP_NAME,
            user_id=_user_id(apartment),
            state={"apartamento": apartment},
        )
        repository.bind_session(session.id, apartment)
        return session.id

    async def _get_session(self, session_id: str) -> tuple[Session, str]:
        apartment = repository.get_session_apartment(session_id)
        if apartment is None:
            raise SessionNotFound(session_id)
        session = await self.session_service.get_session(
            app_name=config.APP_NAME, user_id=_user_id(apartment), session_id=session_id
        )
        if session is None:
            raise SessionNotFound(session_id)
        return session, apartment

    async def list_events(self, session_id: str) -> list[dict[str, Any]]:
        session, _ = await self._get_session(session_id)
        return [
            e.model_dump(mode="json", exclude_none=True, by_alias=True) for e in session.events
        ]

    # ------------------------------------------------------------------ #
    # Conversa
    # ------------------------------------------------------------------ #
    async def send_message(self, session_id: str, text: str) -> TurnResult:
        _, apartment = await self._get_session(session_id)
        message = types.Content(role="user", parts=[types.Part(text=text)])
        async with self._lock(session_id):
            resposta = await self._run(apartment, session_id, message)
        return self._result(session_id, resposta)

    async def respond_confirmation(
        self, session_id: str, confirmation_id: str, approved: bool
    ) -> TurnResult:
        await self._get_session(session_id)
        async with self._lock(session_id):
            conf = repository.respond_confirmation(confirmation_id, session_id, approved)
            if conf is None:
                raise ConfirmationConflict(confirmation_id)
            session, apartment = await self._get_session(session_id)
            request = _find_confirmation_request(session, conf.function_call_id)
            if request is None:
                # Não deveria acontecer: a pendência nasce junto com o evento.
                logger.error("Chamada adk_request_confirmation não encontrada: %s", conf.id)
                resposta = "Não foi possível retomar a ação pendente."
            else:
                request_call_id, author = request
                message = types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=request_call_id,
                                name=REQUEST_CONFIRMATION,
                                response={"confirmed": approved},
                            )
                        )
                    ],
                )
                resposta = await self._run(apartment, session_id, message, agent=author)
            if approved:
                done = repository.get_confirmation_for_call(session_id, conf.function_call_id)
                if done is None or done.executed_at is None:
                    # Falha de retomada nunca é silenciosa.
                    logger.error("Confirmação %s aprovada, mas a tool não executou", conf.id)
                    resposta = "A aprovação foi registrada, mas a ação não pôde ser executada."
        return self._result(session_id, resposta)

    async def _run(
        self,
        apartment: str,
        session_id: str,
        message: types.Content,
        agent: str | None = None,
    ) -> str:
        _TARGET_AGENT.set(agent)
        texts: list[str] = []
        async for event in self.runner.run_async(
            user_id=_user_id(apartment), session_id=session_id, new_message=message
        ):
            texts.extend(_event_texts(event))
        return "\n".join(texts).strip()

    def _result(self, session_id: str, resposta: str) -> TurnResult:
        pending = [
            {"id": c.id, "acao": c.action, "detalhes": c.details}
            for c in repository.pending_confirmations(session_id)
        ]
        if not resposta and pending:
            resposta = "Há ação aguardando sua aprovação pelo aplicativo."
        return TurnResult(resposta=resposta, confirmacoes_pendentes=pending)


def _event_texts(event: Event) -> list[str]:
    if event.author == "user" or event.partial or not event.content or not event.content.parts:
        return []
    return [p.text.strip() for p in event.content.parts if p.text and not p.thought and p.text.strip()]


def _find_confirmation_request(
    session: Session, original_call_id: str
) -> tuple[str, str] | None:
    """(id da chamada ``adk_request_confirmation``, agente autor) da tool pausada."""
    for event in reversed(session.events):
        for call in event.get_function_calls():
            if call.name != REQUEST_CONFIRMATION or not call.args:
                continue
            original = call.args.get("originalFunctionCall") or {}
            if original.get("id") == original_call_id:
                return call.id, event.author
    return None
