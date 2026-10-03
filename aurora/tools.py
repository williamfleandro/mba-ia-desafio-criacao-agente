"""Tools dos especialistas.

Regra de ouro: *o modelo decide o caminho; o código decide o que é permitido*.

* Nenhuma tool recebe "apartamento" como argumento. O apartamento vem de
  :func:`session_apartment`, que lê o vínculo gravado pela API na criação da
  sessão (tabela ``session_apartments``) e confere com o state da sessão
  (Garantia 2).
* Ações com cobrança (taxa > 0) e liberação de acesso pedem confirmação via
  ``tool_context.request_confirmation`` e só executam quando a rota de
  confirmações aprovou a pendência no banco (Garantia 1).
* Respostas de disponibilidade dizem apenas livre/ocupada, nunca de quem é a
  reserva; nenhuma tool devolve dados de outro apartamento.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from google.adk.tools import ToolContext

from . import regulation, repository
from .repository import Confirmation, DomainError

ACTION_RESERVE = "reservar_area"
ACTION_VISITOR = "autorizar_visitante"


class IdentityError(PermissionError):
    pass


def session_apartment(tool_context: ToolContext) -> str:
    """Apartamento autenticado da sessão — nunca vindo do modelo."""
    session_id = tool_context.session.id
    apartment = repository.get_session_apartment(session_id)
    if apartment is None or tool_context.state.get("apartamento") != apartment:
        raise IdentityError("Sessão sem apartamento válido.")
    return apartment


def _error(message: str) -> dict[str, Any]:
    return {"status": "erro", "mensagem": message}


# --------------------------------------------------------------------------- #
# Reservas
# --------------------------------------------------------------------------- #
def list_my_reservations(tool_context: ToolContext) -> dict[str, Any]:
    """Lista as reservas ativas do apartamento do morador desta sessão.

    Returns:
        Reservas ativas (código, área e data) do apartamento do morador.
    """
    apartment = session_apartment(tool_context)
    return {"status": "ok", "reservas": repository.list_active_reservations(apartment)}


def check_area_availability(area: str, data: str, tool_context: ToolContext) -> dict[str, Any]:
    """Verifica se uma área comum está livre em uma data.

    Args:
        area: Id da área (salao-de-festas, churrasqueira ou quadra).
        data: Data no formato AAAA-MM-DD.

    Returns:
        Se a data está livre ou ocupada (sem informar de quem é a reserva).
    """
    session_apartment(tool_context)
    try:
        found = repository.resolve_area(area)
        day = repository.parse_date(data)
    except DomainError as exc:
        return _error(str(exc))
    livre = repository.is_slot_available(found.id, day)
    return {
        "status": "ok",
        "area": found.id,
        "data": day,
        "disponivel": livre,
        "situacao": "livre" if livre else "ocupada",
        "taxa": found.fee,
    }


def _reservation_effect(conn: sqlite3.Connection, conf: Confirmation) -> dict[str, Any]:
    result = repository.insert_reservation(
        conn, conf.apartment, conf.details["area"], conf.details["data"]
    )
    if result.created:
        return {"status": "reservado", "codigo": result.code}
    return {"status": "recusado", "motivo": "data ocupada"}


def create_reservation(area: str, data: str, tool_context: ToolContext) -> dict[str, Any]:
    """Cria uma reserva de área comum para o apartamento do morador.

    Áreas com taxa exigem aprovação do morador pelo aplicativo: nesse caso a
    reserva fica pendente e só é gravada depois da aprovação. Uma frase na
    conversa não substitui essa aprovação.

    Args:
        area: Id da área (salao-de-festas, churrasqueira ou quadra).
        data: Data no formato AAAA-MM-DD.

    Returns:
        Resultado da reserva (código gerado, pendência de confirmação ou recusa).
    """
    apartment = session_apartment(tool_context)
    session_id = tool_context.session.id
    call_id = tool_context.function_call_id or ""

    # Retomada após resposta pela rota de confirmações.
    if tool_context.tool_confirmation is not None:
        return _resume_confirmed(session_id, call_id, _reservation_effect)

    try:
        found = repository.resolve_area(area)
        day = repository.parse_date(data)
    except DomainError as exc:
        return _error(str(exc))

    if found.fee <= 0:
        # Sem cobrança: grava direto. O índice único decide em caso de disputa.
        result = repository.create_reservation(apartment, found.id, day)
        if result.created:
            return {"status": "reservado", "codigo": result.code, "area": found.id, "data": day}
        return {"status": "recusado", "motivo": "data ocupada", "area": found.id, "data": day}

    if not repository.is_slot_available(found.id, day):
        # Não adianta pedir cobrança para uma data já ocupada.
        return {"status": "recusado", "motivo": "data ocupada", "area": found.id, "data": day}

    details = {"area": found.id, "nome_area": found.name, "data": day, "taxa": found.fee}
    return _request_confirmation(
        tool_context,
        apartment,
        ACTION_RESERVE,
        details,
        hint=f"Confirmar reserva de {found.name} em {day} com taxa de R$ {found.fee:.2f}?",
    )


def cancel_my_reservation(area: str, data: str, tool_context: ToolContext) -> dict[str, Any]:
    """Cancela uma reserva ativa do próprio apartamento do morador.

    Args:
        area: Id da área (salao-de-festas, churrasqueira ou quadra).
        data: Data da reserva no formato AAAA-MM-DD.

    Returns:
        Se a reserva do morador foi cancelada ou se não foi encontrada.
    """
    apartment = session_apartment(tool_context)
    try:
        found = repository.resolve_area(area)
        day = repository.parse_date(data)
    except DomainError as exc:
        return _error(str(exc))
    code = repository.cancel_reservation(apartment, found.id, day)
    if code is None:
        return {
            "status": "nao_encontrada",
            "mensagem": "Não existe reserva ativa do seu apartamento para essa área e data.",
        }
    return {"status": "cancelada", "codigo": code, "area": found.id, "data": day}


# --------------------------------------------------------------------------- #
# Visitantes
# --------------------------------------------------------------------------- #
def list_my_visitors(tool_context: ToolContext) -> dict[str, Any]:
    """Lista os visitantes autorizados do apartamento do morador desta sessão.

    Returns:
        Visitantes (nome e data) autorizados para o apartamento do morador.
    """
    apartment = session_apartment(tool_context)
    return {"status": "ok", "visitantes": repository.list_visitors(apartment)}


def _visitor_effect(conn: sqlite3.Connection, conf: Confirmation) -> dict[str, Any]:
    repository.insert_visitor(conn, conf.apartment, conf.details["nome"], conf.details["data"])
    return {"status": "autorizado", "nome": conf.details["nome"], "data": conf.details["data"]}


def authorize_visitor(nome: str, data: str, tool_context: ToolContext) -> dict[str, Any]:
    """Autoriza a entrada de um visitante no prédio para o apartamento do morador.

    Sempre exige aprovação do morador pelo aplicativo; a autorização só é
    gravada depois da aprovação. Uma frase na conversa não substitui essa aprovação.

    Args:
        nome: Nome completo do visitante.
        data: Data da visita no formato AAAA-MM-DD.

    Returns:
        Pendência de confirmação ou resultado da autorização.
    """
    apartment = session_apartment(tool_context)
    session_id = tool_context.session.id
    call_id = tool_context.function_call_id or ""

    if tool_context.tool_confirmation is not None:
        return _resume_confirmed(session_id, call_id, _visitor_effect)

    name = " ".join((nome or "").split())
    if len(name) < 2:
        return _error("Informe o nome do visitante.")
    try:
        day = repository.parse_date(data)
    except DomainError as exc:
        return _error(str(exc))
    details = {"nome": name, "data": day}
    return _request_confirmation(
        tool_context,
        apartment,
        ACTION_VISITOR,
        details,
        hint=f"Confirmar a entrada de {name} em {day}?",
    )


# --------------------------------------------------------------------------- #
# Regulamento
# --------------------------------------------------------------------------- #
def search_regulation(pergunta: str) -> dict[str, Any]:
    """Busca no regulamento interno somente os artigos relevantes para a pergunta.

    Args:
        pergunta: A dúvida do morador sobre o regulamento, em linguagem natural.

    Returns:
        O capítulo e os artigos do regulamento que tratam do assunto perguntado.
    """
    return regulation.search(pergunta)


# --------------------------------------------------------------------------- #
# Confirmação (Garantia 1)
# --------------------------------------------------------------------------- #
def _request_confirmation(
    tool_context: ToolContext,
    apartment: str,
    action: str,
    details: dict[str, Any],
    *,
    hint: str,
) -> dict[str, Any]:
    """Registra a pendência no banco e pausa a execução via ADK."""
    call_id = tool_context.function_call_id
    if not call_id:
        return _error("Não foi possível registrar a confirmação.")
    conf = repository.create_confirmation(
        tool_context.session.id, apartment, call_id, action, details
    )
    tool_context.request_confirmation(
        hint=hint, payload={"confirmacao_id": conf.id, "acao": action, "detalhes": details}
    )
    return {
        "status": "aguardando_confirmacao",
        "confirmacao_id": conf.id,
        "acao": action,
        "detalhes": details,
        "mensagem": "Ação pendente: o morador deve aprovar pela rota de confirmações do app.",
    }


def _resume_confirmed(session_id: str, call_id: str, effect) -> dict[str, Any]:
    """Executa somente o que o BANCO diz que foi aprovado, no máximo uma vez.

    O ``ToolConfirmation`` que o ADK entrega só serve de gatilho: a decisão vem
    do registro da confirmação, cuja transição foi feita pela rota HTTP.
    """
    conf = repository.get_confirmation_for_call(session_id, call_id)
    if conf is None:
        return _error("Confirmação não encontrada; nada foi executado.")
    if conf.status == repository.DENIED:
        return {"status": "negado", "acao": conf.action, "detalhes": conf.details,
                "mensagem": "O morador negou a confirmação; nada foi executado."}
    if conf.status != repository.APPROVED:
        return {"status": "aguardando_confirmacao", "confirmacao_id": conf.id}
    result = repository.execute_confirmed(conf.id, effect)
    if result is None:  # já executada antes
        result = repository.get_confirmation_for_call(session_id, call_id).result or {}
    return {**result, "acao": conf.action, "detalhes": conf.details}
