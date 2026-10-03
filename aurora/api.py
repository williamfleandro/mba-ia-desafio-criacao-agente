"""API HTTP do assistente (contrato do enunciado)."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import repository
from .service import AssistantService, ConfirmationConflict, SessionNotFound

logger = logging.getLogger("aurora.api")


class NovaSessao(BaseModel):
    apartamento: str


class SessaoCriada(BaseModel):
    session_id: str


class Mensagem(BaseModel):
    texto: str


class RespostaConfirmacao(BaseModel):
    id: str
    confirmado: bool


class ConfirmacaoPendente(BaseModel):
    id: str
    acao: str
    detalhes: dict[str, Any]


class RespostaTurno(BaseModel):
    resposta: str
    confirmacoes_pendentes: list[ConfirmacaoPendente]


def create_app(service: AssistantService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service = service or AssistantService()
        yield
        await app.state.service.close()

    app = FastAPI(title="Assistente Virtual — Residencial Aurora", lifespan=lifespan)
    if service is not None:
        app.state.service = service

    def svc(request: Request) -> AssistantService:
        return request.app.state.service

    @app.exception_handler(SessionNotFound)
    async def _not_found(_: Request, __: SessionNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Sessão não encontrada."})

    @app.exception_handler(ConfirmationConflict)
    async def _conflict(_: Request, __: ConfirmationConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": "Não existe confirmação pendente com esse id nesta sessão."},
        )

    @app.post("/sessoes", status_code=201, response_model=SessaoCriada)
    async def criar_sessao(body: NovaSessao, request: Request) -> SessaoCriada:
        apartamento = body.apartamento.strip()
        if not repository.apartment_exists(apartamento):
            raise HTTPException(status_code=422, detail="Apartamento inexistente.")
        return SessaoCriada(session_id=await svc(request).create_session(apartamento))

    @app.post("/sessoes/{session_id}/mensagens", response_model=RespostaTurno)
    async def enviar_mensagem(session_id: str, body: Mensagem, request: Request) -> dict:
        result = await svc(request).send_message(session_id, body.texto)
        return result.as_dict()

    @app.post("/sessoes/{session_id}/confirmacoes", response_model=RespostaTurno)
    async def responder_confirmacao(
        session_id: str, body: RespostaConfirmacao, request: Request
    ) -> dict:
        result = await svc(request).respond_confirmation(session_id, body.id, body.confirmado)
        return result.as_dict()

    @app.get("/sessoes/{session_id}/eventos")
    async def listar_eventos(session_id: str, request: Request) -> list[dict[str, Any]]:
        return await svc(request).list_events(session_id)

    # Rotas de verificação: leem o banco direto, sem modelo.
    @app.get("/apartamentos/{apartamento}/reservas")
    async def reservas(apartamento: str) -> list[dict[str, str]]:
        return repository.list_active_reservations(apartamento)

    @app.get("/apartamentos/{apartamento}/visitantes")
    async def visitantes(apartamento: str) -> list[dict[str, str]]:
        return repository.list_visitors(apartamento)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        "aurora.api:app",
        host=os.getenv("AURORA_HOST", "127.0.0.1"),
        port=int(os.getenv("AURORA_PORT", "8000")),
    )
