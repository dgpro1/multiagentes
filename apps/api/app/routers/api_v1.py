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
from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..api_scopes import (
    CALENDAR_READ,
    CLIENTS_READ,
    CONTACTS_MANAGE,
    CONTACTS_READ,
    INBOX_READ,
    INBOX_REPLY,
    PIPELINE_MANAGE,
    PIPELINE_READ,
)
from ..database import get_db
from ..deps import get_current_user, require
from ..models import Client, Contact, Conversation, Message, User, now_utc
from ..schemas import ClientOut, ContactCreate, ContactUpdate, ConversationPipelineUpdate
from ..services import calendar as calendar_service
from ..services import pipeline as pipeline_service
from ..services.contacts import find_contact, normalize_phone
from ..services.conversation_state import note_reply
from ..services.whatsapp import send_channel_message

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
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    client = _agency_client(db, user, client_id)
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
    return {**_contact_out(contact), "_links": {"self": f"/api/v1/clients/{client.id}/contacts/{contact.id}"}}


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
    content = payload.content.strip()
    if conversation.channel in ("instagram", "messenger"):
        from ..services.social_delivery import queue_message

        message = Message(conversation_id=conversation.id, role="assistant", content=content,
                          sender_type="human", sender_name=user.name)
        db.add(message)
        queue_message(db, conversation, message)
        conversation.updated_at = now_utc()
        db.commit()
        return {**_conversation_out(conversation, request), "message_id": str(message.id)}
    external_message_id = await send_channel_message(db, conversation, content)
    db.add(Message(conversation_id=conversation.id, role="assistant", content=content,
                   sender_type="human", sender_name=user.name, external_message_id=external_message_id))
    note_reply(conversation)
    conversation.updated_at = now_utc()
    db.commit()
    return {**_conversation_out(conversation, request)}


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
