import json
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from ..database import get_db
from ..api_scopes import INBOX_MANAGE, INBOX_READ, INBOX_REPLY, PIPELINE_MANAGE
from ..deps import confined_client_id, get_current_user, require
from ..services.conversation_state import note_reply, set_mode, set_status
from ..models import Agent, Contact, Conversation, Message, now_utc, User
from ..schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationInboxOut,
    ConversationModeUpdate,
    ConversationPipelineUpdate,
    ConversationStatusUpdate,
    ConversationOut,
    CreateNoteRequest,
    LocationSend,
    ReactionRequest,
    SendMessageRequest,
)
from ..services.attachments import (
    MAX_ATTACHMENT_BYTES,
    attachment_kind,
    attachment_response,
    conversation_attachment,
    store_attachment,
)
from ..services.tools import run_completion
from ..services.knowledge import build_system_prompt, llm_turns, retrieve_knowledge
from ..services.operator_media import store_operator_media_reply
from ..services.providers import resolve_agent_credentials
from ..services.usage import record_usage
from ..services.whatsapp import deliver_reaction, resolve_quote, send_channel_location, send_channel_message, signal_channel_read
from ..services import channel_accounts, lead_group, lead_view
from ..services.text_search import folded_like
from ..services.whatsapp_inbound import InboundMessage, resolve_inbound_content


router = APIRouter(prefix="/conversations", tags=["Conversations"])
# The same conversations addressed through their client and short number.
client_router = APIRouter(prefix="/clients", tags=["Conversations"])

MAX_MEDIA_BYTES = MAX_ATTACHMENT_BYTES


def _conversation(db: Session, user: User, conversation_id: uuid.UUID, *, act: bool = False) -> Conversation:
    """The conversation, inside the caller's agency. A thread merged into another
    lead reads as that lead; acting through it (``act``) is refused."""
    query = (
        select(Conversation)
        .options(
            selectinload(Conversation.messages).selectinload(Message.attachments),
            joinedload(Conversation.agent).joinedload(Agent.client),
        )
        .execution_options(populate_existing=True)
        .where(Conversation.id == conversation_id, Conversation.agency_id == user.agency_id)
    )
    # A client's portal admin reaches only its own playground rehearsals (see
    # PortalActor); the client's customer threads are the inbox's business.
    if (only_client := confined_client_id(user)) is not None:
        query = query.where(Conversation.client_id == only_client, Conversation.channel == "playground")
    conversation = db.scalar(query)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.primary_conversation_id is not None:
        if act:
            raise HTTPException(status_code=409, detail=lead_group.ACT_ON_THE_LEAD)
        return _conversation(db, user, conversation.primary_conversation_id)
    return conversation


def _present(db: Session, conversation: Conversation) -> ConversationDetail:
    """The detail of a lead: its own messages plus those of the threads merged into it."""
    return lead_view.with_group(db, conversation, ConversationDetail.model_validate(conversation))


def _respond(db: Session, user: User, conversation_id: uuid.UUID) -> ConversationDetail:
    return _present(db, _conversation(db, user, conversation_id))


@client_router.get(
    "/{client_id}/conversations/number/{number}",
    response_model=ConversationDetail,
    dependencies=[Depends(require(INBOX_READ))],
)
def get_conversation_by_number(
    client_id: uuid.UUID, number: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """One conversation by its per-client number (the "#12" of a lead). The
    agency scoping is the same as the by-id route, plus the client."""
    conversation_id = db.scalar(
        select(Conversation.id).where(
            Conversation.agency_id == user.agency_id,
            Conversation.client_id == client_id,
            Conversation.number == number,
        )
    )
    if conversation_id is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # A number absorbed by a merge is an alias of the lead it joined.
    return _respond(db, user, conversation_id)


@router.get("", response_model=list[ConversationOut], dependencies=[Depends(require(INBOX_READ))])
def list_conversations(
    agent_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(Conversation).where(
        Conversation.agency_id == user.agency_id, Conversation.archived_at.is_(None), lead_group.is_lead_row()
    )
    if (only_client := confined_client_id(user)) is not None:
        query = query.where(Conversation.client_id == only_client, Conversation.channel == "playground")
    if agent_id:
        query = query.where(Conversation.agent_id == agent_id)
    if client_id:
        query = query.where(Conversation.client_id == client_id)
    # Same rule as the inbox: only a new visitor message moves a row up.
    last_inbound = (
        select(lead_group.group_key().label("cid"), func.max(Message.created_at).label("at"))
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.kind == "message", Message.sender_type == "visitor")
        .group_by(lead_group.group_key())
        .subquery()
    )
    query = query.outerjoin(last_inbound, last_inbound.c.cid == Conversation.id).order_by(
        func.coalesce(last_inbound.c.at, Conversation.created_at).desc(), Conversation.created_at.desc()
    )
    items = db.scalars(query).all()
    channel_accounts.annotate(db, items)
    stats = lead_group.group_stats(db, items)
    human_ids = set(
        db.scalars(
            select(Conversation.primary_conversation_id).where(
                Conversation.primary_conversation_id.in_([row.id for row in items]), Conversation.mode == "human"
            )
        ).all()
    )
    return [
        lead_view.list_item(ConversationOut.model_validate(row), row, stats[row.id], group_human=row.id in human_ids)
        for row in items
    ]


@router.get("/inbox", response_model=list[ConversationInboxOut], dependencies=[Depends(require(INBOX_READ))])
def inbox(
    agent_id: uuid.UUID | None = None,
    channel: str | None = None,
    mode: str | None = None,
    search: str | None = None,
    unread: bool = False,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Latest message per conversation, resolved in SQL so we never load full
    # message histories just to build the list.
    # A lead merged from several conversations reads as one row: its threads'
    # messages are grouped under the primary.
    group_key = lead_group.group_key()
    ranked = (
        select(
            group_key.label("cid"),
            Message.content.label("content"),
            Message.sender_type.label("sender_type"),
            Message.created_at.label("created_at"),
            func.row_number().over(partition_by=group_key, order_by=Message.created_at.desc()).label("rn"),
        )
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.kind == "message")
        .subquery()
    )
    last = select(ranked).where(ranked.c.rn == 1).subquery()
    unread_counts = (
        select(group_key.label("cid"), func.count(Message.id).label("n"))
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.sender_type == "visitor",
            Message.is_historical.is_(False),
            or_(Conversation.operator_read_at.is_(None), Message.created_at > Conversation.operator_read_at),
        )
        .group_by(group_key)
    ).subquery()
    unread_count = func.coalesce(unread_counts.c.n, 0)
    last_inbound = (
        select(group_key.label("cid"), func.max(Message.created_at).label("at"))
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.kind == "message", Message.sender_type == "visitor")
        .group_by(group_key)
        .subquery()
    )
    group_human = lead_group.has_human_thread()
    lead_human = or_(Conversation.mode == "human", group_human)

    query = (
        select(Conversation, Agent.name, last.c.content, unread_count.label("unread_count"), last_inbound.c.at.label("last_inbound_at"), group_human.label("group_human"))
        .join(Agent, Agent.id == Conversation.agent_id)
        .outerjoin(last, last.c.cid == Conversation.id)
        .outerjoin(unread_counts, unread_counts.c.cid == Conversation.id)
        .outerjoin(last_inbound, last_inbound.c.cid == Conversation.id)
        .outerjoin(Contact, Contact.id == Conversation.contact_id)
        .where(
            Conversation.agency_id == user.agency_id,
            Conversation.archived_at.is_(None),
            Contact.blocked_at.is_(None),
            lead_group.is_lead_row(),
        )
    )
    if agent_id:
        query = query.where(Conversation.agent_id == agent_id)
    if channel:
        query = query.where(Conversation.channel == channel)
    if mode == "human":
        query = query.where(lead_human)
    elif mode == "ai":
        query = query.where(~lead_human)
    if unread:
        query = query.where(unread_count > 0)
    if search and search.strip():
        query = query.where(
            or_(
                folded_like(Conversation.title, search),
                folded_like(Conversation.contact_name, search),
                folded_like(last.c.content, search),
            )
        )
    # Same rule as the portal: only a new visitor message moves a row up.
    rows = db.execute(
        query.order_by(func.coalesce(last_inbound.c.at, Conversation.created_at).desc(), Conversation.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    channel_accounts.annotate(db, [conv for conv, *_rest in rows])
    stats = lead_group.group_stats(db, [conv for conv, *_rest in rows])
    return [
        {
            "id": conv.id,
            "number": conv.number,
            "agent_id": conv.agent_id,
            "agent_name": agent_name or "",
            "client_id": conv.client_id,
            "title": conv.title,
            "contact_name": conv.contact_name,
            "channel": conv.channel,
            "account_label": conv.account_label,
            "mode": "human" if row_group_human else conv.mode,
            "preview": (content or "")[:140].strip(),
            "unread": int(row_unread_count) > 0,
            "unread_count": int(row_unread_count),
            "updated_at": max(conv.updated_at, stats[conv.id].updated_at or conv.updated_at),
            "last_inbound_at": last_inbound_at,
            "channels": stats[conv.id].channels,
            "linked_count": stats[conv.id].linked_count,
        }
        for conv, agent_name, content, row_unread_count, last_inbound_at, row_group_human in rows
    ]


@router.post("", response_model=ConversationDetail, status_code=status.HTTP_201_CREATED)
def create_conversation(payload: ConversationCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = select(Agent).where(Agent.id == payload.agent_id, Agent.agency_id == user.agency_id, Agent.deleted_at.is_(None))
    if (only_client := confined_client_id(user)) is not None:
        query = query.where(Agent.client_id == only_client)
    agent = db.scalar(query)
    if not agent:
        raise HTTPException(status_code=400, detail="The selected agent does not exist")
    conversation = Conversation(
        agency_id=user.agency_id,
        client_id=agent.client_id,
        agent_id=agent.id,
    )
    db.add(conversation)
    db.commit()
    return _respond(db, user, conversation.id)


@router.post("/{conversation_id}/read", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require(INBOX_MANAGE))])
async def mark_read(conversation_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    conversation = _conversation(db, user, conversation_id)
    # Reading a lead reads every thread merged into it.
    threads = lead_group.group_of(db, conversation)
    now = now_utc()
    for thread in threads:
        thread.operator_read_at = now
    db.commit()
    # Opening the thread is the operator reading it: blue-tick the latest
    # visitor message on WhatsApp too. Best-effort by design.
    for thread in threads:
        latest_external = db.scalar(
            select(Message.external_message_id)
            .where(
                Message.conversation_id == thread.id,
                Message.role == "user",
                Message.external_message_id.is_not(None),
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        if latest_external:
            await signal_channel_read(db, thread, [latest_external], typing=False)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}", response_model=ConversationDetail, dependencies=[Depends(require(INBOX_READ))])
def get_conversation(conversation_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _respond(db, user, conversation_id)


def _ready_agent(db: Session, conversation: Conversation) -> tuple[Agent, tuple[str, str]]:
    """Validate the conversation can produce an AI reply and return the agent + credentials."""
    if conversation.channel in {"instagram", "messenger"}:
        raise HTTPException(status_code=409, detail="Use the inbox reply action for this channel.")
    agent = conversation.agent
    if not agent.is_active:
        raise HTTPException(status_code=400, detail="This agent is inactive")
    credentials = resolve_agent_credentials(db, agent)
    if not credentials or not agent.model.strip():
        raise HTTPException(
            status_code=400,
            detail="This agent is not ready: set its model and add the provider API key in Settings.",
        )
    if conversation.mode == "human":
        raise HTTPException(status_code=409, detail="This conversation is being handled by a person")
    return agent, credentials


async def _generate_reply(
    db: Session,
    user: User,
    conversation: Conversation,
    agent: Agent,
    credentials: tuple[str, str],
    query: str,
) -> Conversation:
    """Run the agent over the current conversation and store the assistant reply."""
    knowledge = await retrieve_knowledge(db, agent, query)
    refreshed = _conversation(db, user, conversation.id)
    exchanged = [item for item in refreshed.messages if item.kind == "message"]
    recent = exchanged[-agent.memory_limit:] if agent.memory_limit else []
    history = llm_turns(recent, agent.prompt_language)
    messages = [{"role": "system", "content": build_system_prompt(agent, knowledge.text)}, *history]
    base_url, api_key = credentials
    completion = await run_completion(
        db, agent, base_url, api_key, messages, temperature=agent.temperature, max_tokens=agent.max_tokens
    )
    note_reply(conversation)
    reply = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=completion.text,
        sources=knowledge.sources,
        tool_calls=completion.tool_calls,
        sender_type="ai",
        sender_name=agent.name,
    )
    db.add(reply)
    record_usage(db, agent.agency_id, agent.id, agent.provider, agent.model.strip(), completion, conversation=conversation, message=reply)
    if completion.attachments:
        # Files a tool returned are stored as their own assistant messages so the
        # playground shows them as document cards.
        from ..services.tool_files import persist_reply_files
        persist_reply_files(db, conversation, agent, completion.attachments)
    conversation.updated_at = now_utc()
    db.commit()
    return _respond(db, user, conversation.id)


@router.post("/{conversation_id}/messages", response_model=ConversationDetail, dependencies=[Depends(require(INBOX_REPLY))])
async def send_message(
    conversation_id: uuid.UUID,
    payload: SendMessageRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = _conversation(db, user, conversation_id, act=True)
    agent, credentials = _ready_agent(db, conversation)

    content = payload.content.strip()
    if not conversation.messages:
        conversation.title = content[:80]
    conversation.updated_at = now_utc()
    db.add(Message(conversation_id=conversation.id, role="user", content=content, sender_type="visitor", sender_name="You"))
    db.commit()
    return await _generate_reply(db, user, conversation, agent, credentials, content)


@router.post("/{conversation_id}/media", response_model=ConversationDetail, dependencies=[Depends(require(INBOX_REPLY))])
async def send_media_message(
    conversation_id: uuid.UUID,
    file: UploadFile = File(...),
    caption: str = Form(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = _conversation(db, user, conversation_id, act=True)
    agent, credentials = _ready_agent(db, conversation)

    content_type = (file.content_type or "").lower() or "application/octet-stream"
    kind = attachment_kind(content_type)
    data = await file.read(MAX_MEDIA_BYTES + 1)
    if len(data) > MAX_MEDIA_BYTES:
        raise HTTPException(status_code=413, detail="The file is too large (20 MB max)")
    if not data:
        raise HTTPException(status_code=400, detail="The file is empty")
    caption = caption.strip()

    # Same orchestration as the WhatsApp channels: the chat keeps the original
    # file as an attachment, the LLM gets a description/transcript (or a
    # placeholder when the capability is off or no OpenAI key is configured).
    message = Message(conversation_id=conversation.id, role="user", content="", sender_type="visitor", sender_name="You")
    display_content, llm_content = await resolve_inbound_content(
        db,
        agent,
        InboundMessage(
            external_message_id="",
            external_chat_id="",
            text=caption,
            media_kind=kind,
            media_bytes=data,
            media_mime=content_type,
        ),
        conversation=conversation,
        message=message,
    )

    if not conversation.messages and caption:
        conversation.title = caption[:80]
    conversation.updated_at = now_utc()
    message.content = display_content
    message.llm_content = llm_content if llm_content != display_content else None
    db.add(message)
    db.flush()
    store_attachment(db, message, data=data, mime=content_type, filename=file.filename, kind=kind)
    db.commit()
    return await _generate_reply(db, user, conversation, agent, credentials, llm_content)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Discard a playground rehearsal. Customer conversations are history and stay."""
    conversation = _conversation(db, user, conversation_id, act=True)
    if conversation.channel != "playground":
        raise HTTPException(status_code=409, detail="Only playground conversations can be deleted")
    db.delete(conversation)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/attachments/{attachment_id}", dependencies=[Depends(require(INBOX_READ))])
def get_attachment(
    conversation_id: uuid.UUID,
    attachment_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = _conversation(db, user, conversation_id)
    return attachment_response(conversation_attachment(db, conversation, attachment_id))


@router.patch("/{conversation_id}/mode", response_model=ConversationDetail, dependencies=[Depends(require(INBOX_MANAGE))])
def set_conversation_mode(
    conversation_id: uuid.UUID,
    payload: ConversationModeUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = _conversation(db, user, conversation_id, act=True)
    changed = set_mode(db, conversation, payload.mode, actor=user.name)
    if changed:
        db.commit()
    return _respond(db, user, conversation_id)


@router.patch("/{conversation_id}/status", response_model=ConversationDetail, dependencies=[Depends(require(INBOX_MANAGE))])
def set_conversation_status(
    conversation_id: uuid.UUID,
    payload: ConversationStatusUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = _conversation(db, user, conversation_id, act=True)
    changed = set_status(db, conversation, payload.status, actor=user.name)
    if changed:
        db.commit()
    return _respond(db, user, conversation_id)


@router.patch("/{conversation_id}/pipeline", response_model=ConversationDetail, dependencies=[Depends(require(PIPELINE_MANAGE))])
def set_conversation_pipeline(
    conversation_id: uuid.UUID,
    payload: ConversationPipelineUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = _conversation(db, user, conversation_id, act=True)
    from ..services import pipeline as pipeline_service
    pipeline_service.move_conversation(
        db, conversation.agent.client, conversation, payload.pipeline_stage_id, payload.deal_value, actor=user.name
    )
    return _respond(db, user, conversation_id)


@router.post("/{conversation_id}/reply", response_model=ConversationDetail, dependencies=[Depends(require(INBOX_REPLY))])
async def reply_as_human(
    conversation_id: uuid.UUID,
    payload: SendMessageRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    lead = _conversation(db, user, conversation_id, act=True)
    conversation = lead_group.thread_in_group(db, lead, payload.via_conversation_id)
    if conversation.channel in ("instagram", "messenger"):
        from ..services.social_delivery import queue_message
        if payload.quoted_message_id:
            raise HTTPException(status_code=422, detail="Quoted replies are not supported by this channel.")
        message = Message(conversation_id=conversation.id, role="assistant", content=payload.content.strip(),
            sender_type="human", sender_name=user.name)
        db.add(message)
        queue_message(db, conversation, message)
        conversation.updated_at = now_utc()
        db.commit()
        return _respond(db, user, conversation_id)
    if conversation.phone_pause_until is not None:
        set_mode(db, conversation, "human")
        db.commit()
    quoted_id, quoted_external = resolve_quote(db, conversation, payload.quoted_message_id)
    external_message_id = await send_channel_message(
        db, conversation, payload.content.strip(), quoted_external_id=quoted_external
    )
    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=payload.content.strip(),
            sender_type="human",
            sender_name=user.name,
            external_message_id=external_message_id,
            quoted_message_id=quoted_id,
        )
    )
    from ..services.phone_handover import cancel_phone_pause
    cancel_phone_pause(conversation)
    note_reply(conversation)
    conversation.updated_at = now_utc()
    db.commit()
    return _respond(db, user, conversation_id)


@router.post("/{conversation_id}/notes", response_model=ConversationDetail, dependencies=[Depends(require(INBOX_REPLY))])
async def add_note(
    conversation_id: uuid.UUID,
    payload: CreateNoteRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Add an internal note to the lead. Notes are visible only to agency/client staff,
    never sent to visitors, excluded from AI context, public API, and metrics."""
    lead = _conversation(db, user, conversation_id, act=True)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="Note content cannot be empty.")
    message = Message(
        conversation_id=lead.id,
        role="assistant",
        kind="note",
        content=content,
        sender_type="human",
        sender_name=user.name,
    )
    db.add(message)
    lead.updated_at = now_utc()
    db.commit()
    return _respond(db, user, conversation_id)


@router.post("/{conversation_id}/location", response_model=ConversationDetail, dependencies=[Depends(require(INBOX_REPLY))])
async def send_location(
    conversation_id: uuid.UUID,
    payload: LocationSend,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Send a pin location as the operator (WhatsApp QR lines through the
    Evolution driver). The chat keeps a location attachment; the phone gets a
    real pin."""
    lead = _conversation(db, user, conversation_id, act=True)
    conversation = lead_group.thread_in_group(db, lead, payload.via_conversation_id)
    external_message_id = await send_channel_location(
        db,
        conversation,
        latitude=payload.latitude,
        longitude=payload.longitude,
        name=payload.name,
        address=payload.address,
    )
    place = {
        "latitude": payload.latitude,
        "longitude": payload.longitude,
        "name": payload.name.strip(),
        "address": payload.address.strip(),
    }
    where = place["name"] or place["address"] or f"{payload.latitude:.6f}, {payload.longitude:.6f}"
    message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=f"📍 {where}",
        sender_type="human",
        sender_name=user.name,
        external_message_id=external_message_id,
    )
    db.add(message)
    db.flush()
    store_attachment(
        db,
        message,
        data=json.dumps(place, separators=(",", ":")).encode(),
        mime="application/json",
        filename="location.json",
        kind="location",
    )
    note_reply(conversation)
    conversation.updated_at = now_utc()
    db.commit()
    return _respond(db, user, conversation_id)


@router.post("/{conversation_id}/messages/{message_id}/reaction", response_model=ConversationDetail, dependencies=[Depends(require(INBOX_REPLY))])
async def react_to_message(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    payload: ReactionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    lead = _conversation(db, user, conversation_id, act=True)
    # The message may live on any thread of the lead; the reaction goes out on that thread.
    target = db.scalar(
        select(Message).where(Message.id == message_id, Message.conversation_id.in_(lead_group.group_ids(db, lead)))
    )
    if not target or (payload.via_conversation_id and target.conversation_id != payload.via_conversation_id):
        raise HTTPException(status_code=404, detail="Message not found")
    if target.role != "user":
        raise HTTPException(status_code=409, detail="Reactions go on the customer's messages")
    emoji = payload.emoji.strip()
    conversation = lead if target.conversation_id == lead.id else db.get(Conversation, target.conversation_id)
    await deliver_reaction(db, conversation, target, emoji)
    target.reaction = emoji or None
    db.commit()
    return _respond(db, user, conversation_id)


@router.post("/{conversation_id}/reply-media", response_model=ConversationDetail, dependencies=[Depends(require(INBOX_REPLY))])
async def reply_media_as_human(
    conversation_id: uuid.UUID,
    file: UploadFile = File(...),
    caption: str = Form(default=""),
    via_conversation_id: uuid.UUID | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    lead = _conversation(db, user, conversation_id, act=True)
    conversation = lead_group.thread_in_group(db, lead, via_conversation_id)
    await store_operator_media_reply(db, conversation, file=file, caption=caption, sender_name=user.name)
    return _respond(db, user, conversation_id)
