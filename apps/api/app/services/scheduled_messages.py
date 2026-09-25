"""Scheduled messages service.

Handles scheduling, listing, cancelling, updating, and background dispatching
of messages deferred to a future time.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Client, Conversation, Message, PortalUser, ScheduledMessage, now_utc
from ..schemas_scheduled_messages import (
    ScheduledMessageCreate,
    ScheduledMessageUpdate,
)
from . import lead_group
from .conversation_state import note_reply, set_mode
from .whatsapp_templates import window_is_open

logger = logging.getLogger(__name__)


def _get_lead(db: Session, client: Client, conversation_id: uuid.UUID) -> Conversation:
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.client_id == client.id,
            Conversation.agency_id == client.agency_id,
        )
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # Always resolve to the lead (primary)
    return lead_group.primary_of(conversation)


def create_scheduled_message(
    db: Session,
    client: Client,
    conversation_id: uuid.UUID,
    data: ScheduledMessageCreate,
    user: PortalUser | None = None,
    sender_name: str | None = None,
) -> ScheduledMessage:
    now = now_utc()
    if data.scheduled_for <= now:
        raise HTTPException(status_code=422, detail="Scheduled time must be in the future")

    lead = _get_lead(db, client, conversation_id)
    if lead.status in ("resolved", "abandoned") or lead.archived_at is not None:
        raise HTTPException(status_code=400, detail="Cannot schedule messages for resolved or archived conversations")

    # If via_conversation_id is provided, verify it belongs to this lead's group
    via_id = data.via_conversation_id
    if via_id is not None:
        target_thread = lead_group.thread_in_group(db, lead, via_id)
        via_id = target_thread.id

    scheduled_msg = ScheduledMessage(
        agency_id=client.agency_id,
        client_id=client.id,
        conversation_id=lead.id,
        via_conversation_id=via_id,
        portal_user_id=user.id if user else None,
        sender_type="human",
        sender_name=sender_name or (user.name if user else None),
        content=data.content,
        scheduled_for=data.scheduled_for,
        status="pending",
    )
    db.add(scheduled_msg)
    db.commit()
    db.refresh(scheduled_msg)
    return scheduled_msg


def list_scheduled_messages(
    db: Session,
    client: Client,
    conversation_id: uuid.UUID,
    status: str | None = None,
) -> list[ScheduledMessage]:
    lead = _get_lead(db, client, conversation_id)
    group_ids = lead_group.group_ids(db, lead)

    query = select(ScheduledMessage).where(
        ScheduledMessage.client_id == client.id,
        ScheduledMessage.conversation_id.in_(group_ids),
    )
    if status:
        query = query.where(ScheduledMessage.status == status)

    query = query.order_by(ScheduledMessage.scheduled_for.asc())
    return list(db.scalars(query).all())


def get_scheduled_message(
    db: Session,
    client: Client,
    conversation_id: uuid.UUID,
    scheduled_id: uuid.UUID,
) -> ScheduledMessage:
    lead = _get_lead(db, client, conversation_id)
    group_ids = lead_group.group_ids(db, lead)

    sm = db.scalar(
        select(ScheduledMessage).where(
            ScheduledMessage.id == scheduled_id,
            ScheduledMessage.client_id == client.id,
            ScheduledMessage.conversation_id.in_(group_ids),
        )
    )
    if not sm:
        raise HTTPException(status_code=404, detail="Scheduled message not found")
    return sm


def cancel_scheduled_message(
    db: Session,
    client: Client,
    conversation_id: uuid.UUID,
    scheduled_id: uuid.UUID,
) -> ScheduledMessage:
    sm = get_scheduled_message(db, client, conversation_id, scheduled_id)
    if sm.status != "pending":
        raise HTTPException(status_code=400, detail="Only pending scheduled messages can be cancelled")

    sm.status = "cancelled"
    sm.updated_at = now_utc()
    db.commit()
    db.refresh(sm)
    return sm


def update_scheduled_message(
    db: Session,
    client: Client,
    conversation_id: uuid.UUID,
    scheduled_id: uuid.UUID,
    data: ScheduledMessageUpdate,
) -> ScheduledMessage:
    sm = get_scheduled_message(db, client, conversation_id, scheduled_id)
    if sm.status != "pending":
        raise HTTPException(status_code=400, detail="Only pending scheduled messages can be edited")

    now = now_utc()
    if data.scheduled_for is not None:
        if data.scheduled_for <= now:
            raise HTTPException(status_code=422, detail="Scheduled time must be in the future")
        sm.scheduled_for = data.scheduled_for

    if data.content is not None:
        sm.content = data.content

    sm.updated_at = now
    db.commit()
    db.refresh(sm)
    return sm


async def dispatch_due_scheduled_messages(db: Session, limit: int = 50) -> int:
    """Dispatches any pending scheduled messages whose scheduled_for time has passed."""
    now = now_utc()
    due_ids = list(
        db.scalars(
            select(ScheduledMessage.id)
            .where(
                ScheduledMessage.status == "pending",
                ScheduledMessage.scheduled_for <= now,
            )
            .order_by(ScheduledMessage.scheduled_for.asc())
            .limit(limit)
        ).all()
    )

    if not due_ids:
        return 0

    dispatched = 0
    for sm_id in due_ids:
        sm = db.scalar(
            select(ScheduledMessage)
            .where(ScheduledMessage.id == sm_id, ScheduledMessage.status == "pending")
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if not sm:
            continue

        try:
            lead = db.get(Conversation, sm.conversation_id)
            if not lead:
                sm.status = "failed"
                sm.failure_reason = "Conversation no longer exists"
                sm.updated_at = now_utc()
                db.commit()
                continue

            # 1. Check if conversation is resolved or archived
            if lead.status in ("resolved", "abandoned") or lead.archived_at is not None:
                sm.status = "failed"
                sm.failure_reason = "Conversation is resolved, abandoned, or archived"
                sm.updated_at = now_utc()
                db.commit()
                continue

            # 2. Check if contact is blocked
            if lead.contact and lead.contact.blocked_at is not None:
                sm.status = "failed"
                sm.failure_reason = "Contact is blocked"
                sm.updated_at = now_utc()
                db.commit()
                continue

            # 3. Resolve target delivery thread in group
            try:
                thread = lead_group.thread_in_group(db, lead, sm.via_conversation_id)
            except Exception:
                thread = lead

            # 4. Check 24-hour messaging window where required
            if thread.channel in ("instagram", "messenger"):
                from .social_policy import require_reply

                try:
                    require_reply(thread, human=True)
                except HTTPException as exc:
                    sm.status = "failed"
                    sm.failure_reason = f"Messaging window closed: {exc.detail}"
                    sm.updated_at = now_utc()
                    db.commit()
                    continue
            elif thread.channel == "whatsapp_cloud":
                stamps = [m.created_at for m in thread.messages if m.kind == "message" and m.sender_type == "visitor"]
                last_inbound = max(stamps) if stamps else None
                if not window_is_open(last_inbound):
                    sm.status = "failed"
                    sm.failure_reason = "24-hour WhatsApp messaging window is closed"
                    sm.updated_at = now_utc()
                    db.commit()
                    continue

            # 5. Deliver through the appropriate channel
            if thread.channel in ("instagram", "messenger"):
                from .social_delivery import queue_message

                msg = Message(
                    conversation_id=thread.id,
                    role="assistant",
                    content=sm.content.strip(),
                    sender_type=sm.sender_type,
                    sender_name=sm.sender_name,
                    portal_user_id=sm.portal_user_id,
                )
                db.add(msg)
                queue_message(db, thread, msg)
                thread.updated_at = now_utc()
            else:
                from .phone_handover import cancel_phone_pause
                from .whatsapp import send_channel_message

                if thread.phone_pause_until is not None:
                    set_mode(db, thread, "human")

                external_message_id = await send_channel_message(db, thread, sm.content.strip())
                msg = Message(
                    conversation_id=thread.id,
                    role="assistant",
                    content=sm.content.strip(),
                    sender_type=sm.sender_type,
                    sender_name=sm.sender_name,
                    portal_user_id=sm.portal_user_id,
                    external_message_id=external_message_id,
                )
                db.add(msg)
                cancel_phone_pause(thread)
                note_reply(thread)
                thread.updated_at = now_utc()

            sm.status = "sent"
            sm.sent_at = now_utc()
            sm.updated_at = now_utc()
            db.commit()
            dispatched += 1

        except Exception as exc:
            db.rollback()
            logger.error("Failed to dispatch scheduled message %s: %s", sm_id, exc)
            try:
                sm = db.get(ScheduledMessage, sm_id)
                if sm:
                    sm.status = "failed"
                    sm.failure_reason = str(exc)
                    sm.updated_at = now_utc()
                    db.commit()
            except Exception:
                db.rollback()

    return dispatched
