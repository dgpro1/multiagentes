"""The versioned public API third parties build on.

Same resources as the panel, with the contract an integrator recognizes:
``/api/v1`` prefix, Kommo-shaped errors (``title``/``type``/``status``/
``detail`` plus ``validation-errors``), ``_links`` on every resource, and
``page``/``limit`` pagination capped at 250 items. Presenters are thin and
deliberately stable: the panel may reshape its own answers, v1 does not.

Authentication and scopes are the same as everywhere else (a bearer token
or a cookie session behind ``require(...)``), so the scope-coverage test
holds here unchanged.
"""

import uuid
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode
import re

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pypdf import PdfReader
from sqlalchemy import Date, cast, delete, func, or_, select
from sqlalchemy.orm import Session, joinedload
from decimal import Decimal

from ..api_scopes import (
    AGENTS_KNOWLEDGE,
    AGENTS_READ,
    AGENTS_WRITE,
    CALENDAR_MANAGE,
    CALENDAR_READ,
    CHANNELS_READ,
    CLIENTS_READ,
    CONTACTS_MANAGE,
    CONTACTS_READ,
    INBOX_READ,
    INBOX_REPLY,
    PIPELINE_MANAGE,
    PIPELINE_READ,
    REPORTS_READ,
)
from ..database import get_db
from ..deps import get_current_user, require
from ..models import (
    Agent,
    AgentQA,
    Client,
    Contact,
    Conversation,
    KnowledgeDocument,
    Message,
    SocialChannel,
    UsageRecord,
    User,
    WhatsAppChannel,
    WhatsAppCloudChannel,
    WidgetChannel,
    now_utc,
)
from ..schemas import AgentCreate, AgentOut, AgentUpdate, ClientOut, ContactCreate, ContactUpdate, ConversationPipelineUpdate, MessageOut, QAPairCreate, QAPairOut, check_reply_delay
from ..schemas_calendar import CalendarMemberCreate, CalendarMemberOut
from ..services import calendar as calendar_service
from ..services import pipeline as pipeline_service
from ..services.contacts import find_contact, normalize_phone
from ..services.conversation_state import note_reply
from ..services.idempotency import abandon, complete, owner_of, use_key
from ..services.knowledge import build_system_prompt, embed_document_chunks, reindex_agent, reindex_document
from ..services.report_operations import ConversationFilters, operations
from ..services.whatsapp import send_channel_message
from .agents import _agent as _panel_agent, _channels_of, _document_out
from .reports import Filters as _ReportFilters, _fold, _group_cost, _grouped, _joined, _money, _replies_query, _reply, _safe_tz

router = APIRouter(prefix="/v1", tags=["Public API v1"])

V1_MAX_LIMIT = 250
V1_DEFAULT_LIMIT = 50


# Error envelope ------------------------------------------------------------


_TITLES = {
    400: ("bad-request", "Bad request"),
    401: ("unauthorized", "Unauthorized"),
    403: ("forbidden", "Forbidden"),
    404: ("not-found", "Not found"),
    409: ("conflict", "Conflict"),
    422: ("validation-failed", "Validation failed"),
    429: ("rate-limited", "Rate limited"),
}


def _error_body(status_code: int, detail, validation_errors: list | None = None) -> dict:
    slug, title = _TITLES.get(status_code, ("error", "Request failed"))
    body = {
        "title": title,
        "type": f"/api/v1/docs/errors#{slug}",
        "status": status_code,
        "detail": detail if isinstance(detail, str) else "Request failed",
    }
    if validation_errors is not None:
        body["validation-errors"] = validation_errors
    return body


def is_v1(request: Request) -> bool:
    return request.url.path.startswith("/api/v1")


async def http_exception_response(request: Request, exc: HTTPException) -> JSONResponse:
    """App-wide HTTPException handler that only restyles v1 paths; every
    other route keeps FastAPI's default ``{"detail": ...}`` shape, including
    OAuth's own ``{"error": ...}`` errors."""
    if not is_v1(request):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        # OAuth-style errors already carry their own machine code.
        return JSONResponse(
            status_code=exc.status_code,
            content={**_error_body(exc.status_code, detail.get("error_description", detail["error"])),
                      "code": detail["error"]},
            headers=exc.headers,
        )
    return JSONResponse(status_code=exc.status_code, content=_error_body(exc.status_code, detail), headers=exc.headers)


async def validation_exception_response(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {
            "field": ".".join(str(part) for part in err["loc"] if part not in ("body", "query", "path")),
            "message": err["msg"],
        }
        for err in exc.errors()
    ]
    if not is_v1(request):
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})
    return JSONResponse(status_code=422, content=_error_body(422, "Validation failed", errors))


# Pagination ----------------------------------------------------------------


def _parse_pagination(page: int, limit: int) -> tuple[int, int]:
    return max(1, page), min(max(1, limit), V1_MAX_LIMIT)


def _page_links(request: Request, page: int, limit: int, total: int) -> dict:
    def url(page_number: int) -> str:
        query = dict(request.query_params)
        query["page"] = str(page_number)
        query["limit"] = str(limit)
        return f"{request.url.path}?{urlencode(query)}"

    last = max(1, -(-total // limit))
    links = {"self": url(page)}
    if page > 1:
        links["prev"] = url(page - 1)
    if page < last:
        links["next"] = url(page + 1)
    return links


def _page(request: Request, rows: list, total: int, page: int, limit: int) -> dict:
    return {"data": rows, "page": page, "limit": limit, "total": total, "_links": _page_links(request, page, limit, total)}


def _self(request: Request) -> dict:
    return {"self": str(request.url).split("?", 1)[0]}


# Shared lookups ------------------------------------------------------------


def _agency_client(db: Session, user, client_id: uuid.UUID) -> Client:
    client = db.scalar(select(Client).where(Client.id == client_id, Client.agency_id == user.agency_id))
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


def _client_conversation(db: Session, client: Client, conversation_id: uuid.UUID) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.client_id != client.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


def _contact_out(contact: Contact) -> dict:
    return {
        "id": str(contact.id),
        "client_id": str(contact.client_id),
        "name": contact.name,
        "phone": contact.phone,
        "email": contact.email,
        "notes": contact.notes,
        "created_at": contact.created_at,
        "updated_at": contact.updated_at,
    }


def _conversation_out(conversation: Conversation, request: Request) -> dict:
    return {
        "id": str(conversation.id),
        "client_id": str(conversation.client_id),
        "agent_id": str(conversation.agent_id),
        "channel": conversation.channel,
        "mode": conversation.mode,
        "status": conversation.status,
        "contact_id": str(conversation.contact_id) if conversation.contact_id else None,
        "contact_name": conversation.contact_name,
        "title": conversation.title,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
        "_links": _self(request),
    }


# Clients -------------------------------------------------------------------


@router.get("/clients", dependencies=[Depends(require(CLIENTS_READ))])
def v1_list_clients(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=V1_DEFAULT_LIMIT, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    page, limit = _parse_pagination(page, limit)
    base = select(Client).where(Client.agency_id == user.agency_id).order_by(Client.name)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.offset((page - 1) * limit).limit(limit)).all()
    data = [{**ClientOut.model_validate(row).model_dump(mode="json"), "_links": {"self": f"/api/v1/clients/{row.id}"}} for row in rows]
    return _page(request, data, total, page, limit)


@router.get("/clients/{client_id}", dependencies=[Depends(require(CLIENTS_READ))])
def v1_get_client(
    request: Request, client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    client = _agency_client(db, user, client_id)
    return {**ClientOut.model_validate(client).model_dump(mode="json"), "_links": _self(request)}


# Contacts ------------------------------------------------------------------


@router.get("/clients/{client_id}/contacts", dependencies=[Depends(require(CONTACTS_READ))])
def v1_list_contacts(
    request: Request,
    client_id: uuid.UUID,
    search: str | None = Query(default=None, max_length=120),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=V1_DEFAULT_LIMIT, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    page, limit = _parse_pagination(page, limit)
    query = select(Contact).where(Contact.client_id == client.id)
    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        query = query.where(or_(func.lower(Contact.name).like(term), Contact.phone.like(f"%{search.strip()}%")))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Contact.updated_at.desc()).offset((page - 1) * limit).limit(limit)).all()
    data = [{**_contact_out(row), "_links": {"self": f"/api/v1/clients/{client.id}/contacts/{row.id}"}} for row in rows]
    return _page(request, data, total, page, limit)


@router.get("/clients/{client_id}/contacts/{contact_id}", dependencies=[Depends(require(CONTACTS_READ))])
def v1_get_contact(
    request: Request, client_id: uuid.UUID, contact_id: uuid.UUID,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    contact = db.get(Contact, contact_id)
    if contact is None or contact.client_id != client.id:
        raise HTTPException(status_code=404, detail="Contact not found")
    return {**_contact_out(contact), "_links": _self(request)}


@router.post("/clients/{client_id}/contacts", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require(CONTACTS_MANAGE))])
def v1_create_contact(
    request: Request, client_id: uuid.UUID, payload: ContactCreate,
    idempotency_key: str | None = Header(default=None),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    receipt = use_key(db, agency_id=user.agency_id, owner=owner_of(user), key=idempotency_key or "",
                      endpoint="v1:create_contact", body=payload.model_dump())
    if receipt.replay is not None:
        replay = receipt.replay
        return JSONResponse(status_code=replay["status"], content=replay["body"])
    try:
        phone = normalize_phone(payload.phone)
        if not phone:
            raise HTTPException(status_code=422, detail="Enter a phone number with its country code")
        if find_contact(db, client.id, phone):
            raise HTTPException(status_code=409, detail="A contact with this phone number already exists")
        contact = Contact(
            client_id=client.id, name=payload.name.strip(), phone=phone,
            email=payload.email or None, notes=payload.notes.strip(),
        )
        db.add(contact)
        db.commit()
        db.refresh(contact)
        body = jsonable_encoder({**_contact_out(contact), "_links": {"self": f"/api/v1/clients/{client.id}/contacts/{contact.id}"}})
        complete(db, receipt, status=201, body=body)
        return JSONResponse(status_code=201, content=body)
    except HTTPException:
        abandon(db, receipt)
        raise


@router.patch("/clients/{client_id}/contacts/{contact_id}", dependencies=[Depends(require(CONTACTS_MANAGE))])
def v1_update_contact(
    request: Request, client_id: uuid.UUID, contact_id: uuid.UUID, payload: ContactUpdate,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    contact = db.get(Contact, contact_id)
    if contact is None or contact.client_id != client.id:
        raise HTTPException(status_code=404, detail="Contact not found")
    if payload.name is not None:
        contact.name = payload.name.strip()
    if payload.phone is not None:
        phone = normalize_phone(payload.phone)
        if not phone:
            raise HTTPException(status_code=422, detail="Enter a phone number with its country code")
        other = find_contact(db, client.id, phone)
        if other is not None and other.id != contact.id:
            raise HTTPException(status_code=409, detail="A contact with this phone number already exists")
        contact.phone = phone
    if payload.email is not None:
        contact.email = payload.email or None
    if payload.notes is not None:
        contact.notes = payload.notes.strip()
    db.commit()
    db.refresh(contact)
    return {**_contact_out(contact), "_links": _self(request)}


# Conversations ---------------------------------------------------------------


class V1Reply(BaseModel):
    content: str = Field(min_length=1, max_length=50000)


@router.get("/clients/{client_id}/conversations", dependencies=[Depends(require(INBOX_READ))])
def v1_list_conversations(
    request: Request,
    client_id: uuid.UUID,
    status: str | None = Query(default=None, max_length=20),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=V1_DEFAULT_LIMIT, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    page, limit = _parse_pagination(page, limit)
    query = select(Conversation).where(Conversation.client_id == client.id, Conversation.archived_at.is_(None))
    if status in ("open", "resolved"):
        query = query.where(Conversation.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Conversation.updated_at.desc()).offset((page - 1) * limit).limit(limit)).all()
    return _page(request, [_conversation_out(row, request) for row in rows], total, page, limit)


@router.get("/clients/{client_id}/conversations/{conversation_id}", dependencies=[Depends(require(INBOX_READ))])
def v1_get_conversation(
    request: Request, client_id: uuid.UUID, conversation_id: uuid.UUID,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    return _conversation_out(_client_conversation(db, client, conversation_id), request)


@router.post("/clients/{client_id}/conversations/{conversation_id}/reply", dependencies=[Depends(require(INBOX_REPLY))])
async def v1_reply(
    request: Request, client_id: uuid.UUID, conversation_id: uuid.UUID, payload: V1Reply,
    idempotency_key: str | None = Header(default=None),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """Answer as the operator, with the panel's own rules: the case must be
    in human hands, and social lines queue through their durable outbox."""
    client = _agency_client(db, user, client_id)
    conversation = _client_conversation(db, client, conversation_id)
    if conversation.mode != "human":
        raise HTTPException(status_code=409, detail="Take control of the conversation before replying")
    if conversation.status != "open":
        raise HTTPException(status_code=409, detail="This conversation is resolved")
    receipt = use_key(db, agency_id=user.agency_id, owner=owner_of(user), key=idempotency_key or "",
                      endpoint="v1:reply", body={**payload.model_dump(), "conversation_id": str(conversation_id)})
    if receipt.replay is not None:
        replay = receipt.replay
        return JSONResponse(status_code=replay["status"], content=replay["body"])
    try:
        content = payload.content.strip()
        if conversation.channel in ("instagram", "messenger"):
            from ..services.social_delivery import queue_message

            message = Message(conversation_id=conversation.id, role="assistant", content=content,
                              sender_type="human", sender_name=user.name)
            db.add(message)
            queue_message(db, conversation, message)
            conversation.updated_at = now_utc()
            db.commit()
            body = jsonable_encoder({**_conversation_out(conversation, request), "message_id": str(message.id)})
            complete(db, receipt, status=200, body=body)
            return JSONResponse(status_code=200, content=body)
        external_message_id = await send_channel_message(db, conversation, content)
        db.add(Message(conversation_id=conversation.id, role="assistant", content=content,
                       sender_type="human", sender_name=user.name, external_message_id=external_message_id))
        note_reply(conversation)
        conversation.updated_at = now_utc()
        db.commit()
        body = jsonable_encoder(_conversation_out(conversation, request))
        complete(db, receipt, status=200, body=body)
        return JSONResponse(status_code=200, content=body)
    except HTTPException:
        abandon(db, receipt)
        raise


# Pipeline --------------------------------------------------------------------


@router.get("/clients/{client_id}/pipeline/board", dependencies=[Depends(require(PIPELINE_READ))])
def v1_pipeline_board(
    request: Request, client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    client = _agency_client(db, user, client_id)
    board = pipeline_service.board(db, client)
    return {**board, "_links": _self(request)}


@router.patch("/clients/{client_id}/conversations/{conversation_id}/pipeline", dependencies=[Depends(require(PIPELINE_MANAGE))])
def v1_move_deal(
    request: Request, client_id: uuid.UUID, conversation_id: uuid.UUID, payload: ConversationPipelineUpdate,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    conversation = _client_conversation(db, client, conversation_id)
    pipeline_service.move_conversation(db, client, conversation, payload.pipeline_stage_id, payload.deal_value, actor=user.name)
    card = pipeline_service.board(db, client)
    moved = next((item for item in card["cards"] if str(item["id"]) == str(conversation_id)), None)
    return {**(moved or {}), "_links": _self(request)}


# Calendar ----------------------------------------------------------------------


@router.get("/clients/{client_id}/calendar/events", dependencies=[Depends(require(CALENDAR_READ))])
async def v1_calendar_events(
    request: Request,
    client_id: uuid.UUID,
    start: datetime = Query(...),
    end: datetime = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    result = await calendar_service.events(db, client, start, end)
    events = result.get("events", result) if isinstance(result, dict) else result
    errors = result.get("errors", []) if isinstance(result, dict) else []
    total = len(events) if isinstance(events, list) else 0
    return {"data": events, "errors": errors, "page": 1, "limit": total, "total": total, "_links": _self(request)}


@router.get("/clients/{client_id}/calendar", dependencies=[Depends(require(CALENDAR_READ))])
def v1_calendar_overview(
    request: Request, client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """The client's calendars: connection state plus a fresh link per member."""
    client = _agency_client(db, user, client_id)
    return {**jsonable_encoder(calendar_service.overview(db, client)), "_links": _self(request)}


@router.post("/clients/{client_id}/calendar/members", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require(CALENDAR_MANAGE))])
def v1_calendar_member_create(
    request: Request, client_id: uuid.UUID, payload: CalendarMemberCreate,
    idempotency_key: str | None = Header(default=None),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    receipt = use_key(db, agency_id=user.agency_id, owner=owner_of(user), key=idempotency_key or "",
                      endpoint="v1:create_calendar_member", body={**payload.model_dump(), "client_id": str(client_id)})
    if receipt.replay is not None:
        replay = receipt.replay
        return JSONResponse(status_code=replay["status"], content=replay["body"])
    try:
        member = calendar_service.create_member(db, client, payload)
        body = jsonable_encoder({**CalendarMemberOut.model_validate(calendar_service.member_out(member)).model_dump(mode="json"),
                                 "_links": {"self": f"/api/v1/clients/{client.id}/calendar"}})
        complete(db, receipt, status=201, body=body)
        return JSONResponse(status_code=201, content=body)
    except HTTPException:
        abandon(db, receipt)
        raise


@router.delete("/clients/{client_id}/calendar/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require(CALENDAR_MANAGE))])
async def v1_calendar_member_delete(
    client_id: uuid.UUID, member_id: uuid.UUID,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    await calendar_service.delete_member(db, client, member_id)
    return JSONResponse(status_code=204, content=None)


# Agents ----------------------------------------------------------------------


def _client_agent(db: Session, user: User, client: Client, agent_id: uuid.UUID) -> Agent:
    agent = _panel_agent(db, user, agent_id)
    if agent.client_id != client.id:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


def _agent_out(agent: Agent) -> dict:
    return {**AgentOut.model_validate(agent).model_dump(mode="json"),
            "_links": {"self": f"/api/v1/clients/{agent.client_id}/agents/{agent.id}"}}


@router.get("/clients/{client_id}/agents", dependencies=[Depends(require(AGENTS_READ))])
def v1_list_agents(
    request: Request,
    client_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=V1_DEFAULT_LIMIT, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    page, limit = _parse_pagination(page, limit)
    base = select(Agent).where(Agent.client_id == client.id, Agent.agency_id == user.agency_id, Agent.deleted_at.is_(None))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.options(joinedload(Agent.client)).order_by(Agent.created_at.desc()).offset((page - 1) * limit).limit(limit)).unique().all()
    return _page(request, [_agent_out(row) for row in rows], total, page, limit)


@router.post("/clients/{client_id}/agents", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require(AGENTS_WRITE))])
def v1_create_agent(
    request: Request, client_id: uuid.UUID, payload: AgentCreate,
    idempotency_key: str | None = Header(default=None),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    if payload.client_id != client.id:
        raise HTTPException(status_code=409, detail="The agent's client must match the path")
    receipt = use_key(db, agency_id=user.agency_id, owner=owner_of(user), key=idempotency_key or "",
                      endpoint="v1:create_agent", body=payload.model_dump(mode="json"))
    if receipt.replay is not None:
        replay = receipt.replay
        return JSONResponse(status_code=replay["status"], content=replay["body"])
    try:
        agent = Agent(agency_id=user.agency_id, **payload.model_dump())
        db.add(agent)
        db.commit()
        body = jsonable_encoder(_agent_out(_panel_agent(db, user, agent.id)))
        complete(db, receipt, status=201, body=body)
        return JSONResponse(status_code=201, content=body)
    except HTTPException:
        abandon(db, receipt)
        raise


@router.get("/clients/{client_id}/agents/{agent_id}", dependencies=[Depends(require(AGENTS_READ))])
def v1_get_agent(
    request: Request, client_id: uuid.UUID, agent_id: uuid.UUID,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    return _agent_out(_client_agent(db, user, client, agent_id))


@router.patch("/clients/{client_id}/agents/{agent_id}", dependencies=[Depends(require(AGENTS_WRITE))])
def v1_update_agent(
    request: Request, client_id: uuid.UUID, agent_id: uuid.UUID, payload: AgentUpdate,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    agent = _client_agent(db, user, client, agent_id)
    values = payload.model_dump(exclude_unset=True)
    if values.get("client_id", agent.client_id) != agent.client_id:
        raise HTTPException(status_code=409, detail="An agent cannot move clients through this API")
    try:
        check_reply_delay(
            values.get("reply_delay_min_seconds", agent.reply_delay_min_seconds),
            values.get("reply_delay_max_seconds", agent.reply_delay_max_seconds),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for key, value in values.items():
        setattr(agent, key, value)
    db.commit()
    return _agent_out(_panel_agent(db, user, agent.id))


@router.delete("/clients/{client_id}/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require(AGENTS_WRITE))])
def v1_delete_agent(
    client_id: uuid.UUID, agent_id: uuid.UUID,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """Delete the agent's configuration and knowledge; keep its conversations."""
    from ..models import AgentTool, EscalationRule, KnowledgeChunk

    client = _agency_client(db, user, client_id)
    agent = _client_agent(db, user, client, agent_id)
    if _channels_of(db, agent):
        raise HTTPException(
            status_code=409,
            detail="This agent answers a channel of its client. Assign another agent to it before deleting this one.",
        )
    for model in (AgentTool, AgentQA, KnowledgeChunk, KnowledgeDocument, EscalationRule):
        db.execute(delete(model).where(model.agent_id == agent.id))
    for field in ("instructions", "personality", "brief_summary", "brief_products", "brief_audience", "brief_policies", "brief_dos", "brief_donts"):
        setattr(agent, field, "")
    agent.escalation_team_id = None
    agent.escalation_assignee_id = None
    agent.is_active = False
    agent.deleted_at = now_utc()
    db.commit()
    return JSONResponse(status_code=204, content=None)


@router.get("/clients/{client_id}/agents/{agent_id}/prompt", dependencies=[Depends(require(AGENTS_READ))])
def v1_agent_prompt(
    request: Request, client_id: uuid.UUID, agent_id: uuid.UUID,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """What the model receives on every message, minus the knowledge retrieved per message."""
    client = _agency_client(db, user, client_id)
    agent = _client_agent(db, user, client, agent_id)
    return {"prompt": build_system_prompt(agent, ""), "_links": _self(request)}


# Knowledge ---------------------------------------------------------------------

MAX_PDF_BYTES = 20 * 1024 * 1024


def _document_out_v1(client: Client, agent: Agent, doc) -> dict:
    return {**jsonable_encoder(_document_out(doc)),
            "_links": {"self": f"/api/v1/clients/{client.id}/agents/{agent.id}/documents/{doc.id}"}}


@router.get("/clients/{client_id}/agents/{agent_id}/documents", dependencies=[Depends(require(AGENTS_READ))])
def v1_list_documents(
    client_id: uuid.UUID, agent_id: uuid.UUID,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    agent = _client_agent(db, user, client, agent_id)
    docs = db.scalars(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.agent_id == agent.id)
        .order_by(KnowledgeDocument.created_at.desc())
    ).all()
    return {"data": [_document_out_v1(client, agent, doc) for doc in docs], "total": len(docs),
            "_links": {"self": f"/api/v1/clients/{client.id}/agents/{agent.id}/documents"}}


@router.post("/clients/{client_id}/agents/{agent_id}/documents", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require(AGENTS_KNOWLEDGE))])
async def v1_upload_document(
    client_id: uuid.UUID, agent_id: uuid.UUID, file: UploadFile = File(...),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """Teach the agent from a PDF, with the panel's own rules: best-effort indexing, never a failed upload."""
    client = _agency_client(db, user, client_id)
    agent = _client_agent(db, user, client, agent_id)
    original_name = file.filename or "document.pdf"
    if file.content_type != "application/pdf" and not original_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    data = await file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="The PDF exceeds the 20 MB limit")
    safe_name = re.sub(r"[^\w. -]", "_", Path(original_name).name)[:220] or "document.pdf"
    document = KnowledgeDocument(agent_id=agent.id, filename=safe_name, file_data=data, status="processed")
    try:
        reader = PdfReader(BytesIO(data))
        document.extracted_text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
        if not document.extracted_text:
            document.status = "error"
            document.error_message = "No extractable text was found in the PDF. It may be a scanned document."
    except Exception:
        document.status = "error"
        document.error_message = "The PDF could not be processed. Check that the file is not damaged or protected."
    db.add(document)
    db.commit()
    db.refresh(document)
    if document.status == "processed":
        try:
            await embed_document_chunks(db, agent, document)
        except Exception:
            db.rollback()
    body = _document_out_v1(client, agent, document)
    return JSONResponse(status_code=201, content=body)


@router.delete("/clients/{client_id}/agents/{agent_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require(AGENTS_KNOWLEDGE))])
def v1_delete_document(
    client_id: uuid.UUID, agent_id: uuid.UUID, document_id: uuid.UUID,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    agent = _client_agent(db, user, client, agent_id)
    document = db.scalar(
        select(KnowledgeDocument).where(KnowledgeDocument.id == document_id, KnowledgeDocument.agent_id == agent.id)
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    db.delete(document)
    db.commit()
    return JSONResponse(status_code=204, content=None)


@router.post("/clients/{client_id}/agents/{agent_id}/documents/reindex", dependencies=[Depends(require(AGENTS_KNOWLEDGE))])
async def v1_reindex_documents(
    client_id: uuid.UUID, agent_id: uuid.UUID,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """Re-embed every processed document with the agent's current embedding model."""
    client = _agency_client(db, user, client_id)
    agent = _client_agent(db, user, client, agent_id)
    try:
        await reindex_agent(db, agent)
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"Could not index the documents: {exc}.") from exc
    db.expire_all()
    docs = db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.agent_id == agent.id).order_by(KnowledgeDocument.created_at.desc())).all()
    return {"data": [_document_out_v1(client, agent, doc) for doc in docs], "total": len(docs),
            "_links": {"self": f"/api/v1/clients/{client.id}/agents/{agent.id}/documents"}}


@router.get("/clients/{client_id}/agents/{agent_id}/qa", dependencies=[Depends(require(AGENTS_READ))])
def v1_list_qa(
    client_id: uuid.UUID, agent_id: uuid.UUID,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    agent = _client_agent(db, user, client, agent_id)
    rows = db.scalars(select(AgentQA).where(AgentQA.agent_id == agent.id).order_by(AgentQA.position, AgentQA.created_at)).all()
    return {"data": [{**QAPairOut.model_validate(row).model_dump(mode="json"),
                      "_links": {"self": f"/api/v1/clients/{client.id}/agents/{agent.id}/qa/{row.id}"}} for row in rows],
            "total": len(rows), "_links": {"self": f"/api/v1/clients/{client.id}/agents/{agent.id}/qa"}}


@router.post("/clients/{client_id}/agents/{agent_id}/qa", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require(AGENTS_KNOWLEDGE))])
def v1_create_qa(
    client_id: uuid.UUID, agent_id: uuid.UUID, payload: QAPairCreate,
    idempotency_key: str | None = Header(default=None),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    agent = _client_agent(db, user, client, agent_id)
    receipt = use_key(db, agency_id=user.agency_id, owner=owner_of(user), key=idempotency_key or "",
                      endpoint="v1:create_qa", body={**payload.model_dump(), "agent_id": str(agent_id)})
    if receipt.replay is not None:
        replay = receipt.replay
        return JSONResponse(status_code=replay["status"], content=replay["body"])
    try:
        position = db.scalar(select(func.count(AgentQA.id)).where(AgentQA.agent_id == agent.id)) or 0
        pair = AgentQA(agent_id=agent.id, question=payload.question.strip(), answer=payload.answer.strip(), position=position)
        db.add(pair)
        db.commit()
        db.refresh(pair)
        body = jsonable_encoder({**QAPairOut.model_validate(pair).model_dump(mode="json"),
                                 "_links": {"self": f"/api/v1/clients/{client.id}/agents/{agent.id}/qa/{pair.id}"}})
        complete(db, receipt, status=201, body=body)
        return JSONResponse(status_code=201, content=body)
    except HTTPException:
        abandon(db, receipt)
        raise


@router.delete("/clients/{client_id}/agents/{agent_id}/qa/{qa_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require(AGENTS_KNOWLEDGE))])
def v1_delete_qa(
    client_id: uuid.UUID, agent_id: uuid.UUID, qa_id: uuid.UUID,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    agent = _client_agent(db, user, client, agent_id)
    pair = db.scalar(select(AgentQA).where(AgentQA.id == qa_id, AgentQA.agent_id == agent.id))
    if not pair:
        raise HTTPException(status_code=404, detail="Q&A pair not found")
    db.delete(pair)
    db.commit()
    return JSONResponse(status_code=204, content=None)


# Channels ----------------------------------------------------------------------


def _channel_row(channel_type: str, row, label: str | None, status: str, connected: bool) -> dict:
    return {
        "id": str(row.id),
        "client_id": str(row.client_id),
        "type": channel_type,
        "label": label,
        "status": status,
        "connected": connected,
        "agent_id": str(row.agent_id) if row.agent_id else None,
        "last_error": row.last_error,
        "last_connected_at": row.last_connected_at,
        "_links": {"self": f"/api/v1/clients/{row.client_id}/channels"},
    }


@router.get("/clients/{client_id}/channels", dependencies=[Depends(require(CHANNELS_READ))])
def v1_list_channels(
    request: Request, client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Every line of the client in one place: QR and API WhatsApp numbers,
    Instagram and Messenger accounts, and the web chat. Read-only: connecting
    a line stays a panel gesture."""
    client = _agency_client(db, user, client_id)
    rows: list[dict] = []
    for row in db.scalars(select(WhatsAppChannel).where(WhatsAppChannel.client_id == client.id).order_by(WhatsAppChannel.created_at)).all():
        rows.append(_channel_row("whatsapp_qr", row, row.label or row.display_name or row.phone_number,
                                 row.status, row.status == "connected" and row.is_enabled))
    for row in db.scalars(select(WhatsAppCloudChannel).where(WhatsAppCloudChannel.client_id == client.id).order_by(WhatsAppCloudChannel.created_at)).all():
        rows.append(_channel_row("whatsapp_cloud", row, row.label or row.display_name or row.phone_number,
                                 row.status, row.status == "connected" and row.is_enabled))
    for row in db.scalars(select(SocialChannel).where(SocialChannel.client_id == client.id).order_by(SocialChannel.created_at)).all():
        rows.append(_channel_row(row.provider, row, row.label or row.username or row.display_name,
                                 row.status, row.status == "connected" and row.is_enabled))
    widget = db.scalar(select(WidgetChannel).where(WidgetChannel.client_id == client.id))
    if widget is not None:
        rows.append(_channel_row("webchat", widget, None, "connected" if widget.is_enabled else "disconnected", widget.is_enabled))
    return {"data": jsonable_encoder(rows), "total": len(rows), "_links": _self(request)}


# Messages ------------------------------------------------------------------------


@router.get("/clients/{client_id}/conversations/{conversation_id}/messages", dependencies=[Depends(require(INBOX_READ))])
def v1_list_messages(
    request: Request,
    client_id: uuid.UUID,
    conversation_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=V1_DEFAULT_LIMIT, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The thread oldest-first, visitor turns and operator notes alike:
    ``kind`` tells a message from an ``activity`` line."""
    client = _agency_client(db, user, client_id)
    conversation = _client_conversation(db, client, conversation_id)
    page, limit = _parse_pagination(page, limit)
    base = select(Message).where(Message.conversation_id == conversation.id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.order_by(Message.created_at.asc()).offset((page - 1) * limit).limit(limit)).all()
    data = [{**MessageOut.model_validate(row).model_dump(mode="json"),
             "_links": {"self": f"/api/v1/clients/{client.id}/conversations/{conversation.id}/messages/{row.id}"}}
            for row in rows]
    return _page(request, data, total, page, limit)


# Reports -------------------------------------------------------------------------


def _report_agent(db: Session, client: Client, agent_id: uuid.UUID | None) -> uuid.UUID | None:
    if agent_id is None:
        return None
    agent = db.get(Agent, agent_id)
    if agent is None or agent.client_id != client.id:
        raise HTTPException(status_code=400, detail="The agent does not belong to this client")
    return agent.id


@router.get("/clients/{client_id}/reports/costs", dependencies=[Depends(require(REPORTS_READ))])
def v1_report_costs(
    request: Request,
    client_id: uuid.UUID,
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    agent_id: uuid.UUID | None = None,
    model: str | None = None,
    tz: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
    agent_id = _report_agent(db, client, agent_id)
    filters = _ReportFilters(user.agency_id, date_from, date_to, client.id, agent_id, model, None)
    zone = _safe_tz(tz)
    by_model = _fold(_grouped(db, filters, UsageRecord.model), 1, lambda key: (key[0], key[0]))
    replies = sum(entry["replies"] for entry in by_model)
    cost = sum((Decimal(str(entry["cost_usd"])) for entry in by_model), Decimal(0))
    conversations = db.scalar(filters.apply(_joined(select(func.count(func.distinct(UsageRecord.conversation_id)))))) or 0
    days: dict = {}
    for row in _grouped(db, filters, cast(func.timezone(zone, UsageRecord.created_at), Date)):
        day, day_model, day_replies, _tokens_in, _tokens_out, metered, unpriced_in, unpriced_out = row
        entry = days.setdefault(day.isoformat(), {"replies": 0, "cost": Decimal(0)})
        entry["replies"] += int(day_replies)
        entry["cost"] += _group_cost(metered, day_model, unpriced_in, unpriced_out)
    return {
        "totals": {
            "cost_usd": _money(cost),
            "replies": replies,
            "conversations": int(conversations),
            "input_tokens": sum(entry["input_tokens"] for entry in by_model),
            "output_tokens": sum(entry["output_tokens"] for entry in by_model),
            "avg_cost_per_reply_usd": _money(cost / replies) if replies else 0.0,
        },
        "by_agent": _fold(_grouped(db, filters, UsageRecord.agent_id, Agent.name), 2,
                          lambda key: (str(key[0]) if key[0] else None, key[1] or "")),
        "by_model": by_model,
        "by_day": [{"date": day, "replies": entry["replies"], "cost_usd": _money(entry["cost"])}
                   for day, entry in sorted(days.items())],
        "tz": zone,
        "_links": _self(request),
    }


@router.get("/clients/{client_id}/reports/replies", dependencies=[Depends(require(REPORTS_READ))])
def v1_report_replies(
    request: Request,
    client_id: uuid.UUID,
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    agent_id: uuid.UUID | None = None,
    model: str | None = None,
    search: str | None = Query(default=None, max_length=120),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=V1_DEFAULT_LIMIT, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """One line per reply, newest first. The panel's CSV export stays in the panel."""
    client = _agency_client(db, user, client_id)
    agent_id = _report_agent(db, client, agent_id)
    page, limit = _parse_pagination(page, limit)
    filters = _ReportFilters(user.agency_id, date_from, date_to, client.id, agent_id, model, search)
    query = _replies_query(filters)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    items = [_reply(row) for row in db.execute(query.limit(limit).offset((page - 1) * limit)).all()]
    return _page(request, jsonable_encoder(items), total, page, limit)


@router.get("/clients/{client_id}/reports/operations", dependencies=[Depends(require(REPORTS_READ))])
def v1_report_operations(
    request: Request,
    client_id: uuid.UUID,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    agent_id: uuid.UUID | None = None,
    channel: str | None = None,
    model: str | None = None,
    tz: str | None = None,
    bucket: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """How the agents attended the range's conversations: totals, timing and message volume."""
    client = _agency_client(db, user, client_id)
    agent_id = _report_agent(db, client, agent_id)
    filters = ConversationFilters(
        user.agency_id, date_from=date_from, date_to=date_to, client_id=client.id,
        agent_id=agent_id, channel=channel, tz=tz, model=model, bucket=bucket,
    )
    return {**jsonable_encoder(operations(db, filters)), "_links": _self(request)}
