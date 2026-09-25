"""Linked threads: the conversations a lead merge folded into one lead.

A lead is a ``Conversation`` row: the *primary*. When another lead is merged into
it, that row is not deleted. It becomes a *linked thread*: it keeps its channel,
its chat key and its messages (nothing is ever moved) and keeps receiving what
its channel delivers, but ``primary_conversation_id`` points at the primary, it
is hidden from lists, pipeline and reports, and the lead-level data (stage,
budget, custom values, status, assignee...) is read from the primary. Groups
never chain: a primary is never itself linked.

Everything that needs to know about the group goes through this module, so the
rule lives in one place.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, aliased

from ..models import Conversation

ACT_ON_THE_LEAD = "This conversation was merged into another lead: act on the lead"


def is_linked(conversation: Conversation) -> bool:
    """Whether this row was absorbed by a lead merge."""
    return conversation.primary_conversation_id is not None


def primary_of(conversation: Conversation) -> Conversation:
    """The lead a conversation belongs to: itself unless it is a linked thread."""
    if conversation.primary_conversation_id is None:
        return conversation
    return conversation.primary_thread or conversation


def linked_threads(db: Session, primary: Conversation) -> list[Conversation]:
    """The threads absorbed by ``primary``, oldest first."""
    return list(
        db.scalars(
            select(Conversation)
            .where(Conversation.primary_conversation_id == primary.id)
            .order_by(Conversation.created_at, Conversation.id)
        ).all()
    )


def group_of(db: Session, conversation: Conversation) -> list[Conversation]:
    """The lead and every thread linked to it, primary first."""
    primary = primary_of(conversation)
    return [primary, *linked_threads(db, primary)]


def group_ids(db: Session, conversation: Conversation) -> list[uuid.UUID]:
    primary_id = conversation.primary_conversation_id or conversation.id
    ids = db.scalars(select(Conversation.id).where(Conversation.primary_conversation_id == primary_id)).all()
    return [primary_id, *ids]


def alias_target(db: Session, conversation: Conversation) -> Conversation:
    """What a read of ``conversation`` answers with: the lead, for a thread that
    was merged into one."""
    if conversation.primary_conversation_id is None:
        return conversation
    return db.get(Conversation, conversation.primary_conversation_id) or conversation


def require_lead(conversation: Conversation) -> Conversation:
    """Refuse to act directly on an absorbed thread: the action belongs to the lead."""
    if is_linked(conversation):
        raise HTTPException(status_code=409, detail=ACT_ON_THE_LEAD)
    return conversation


def thread_in_group(db: Session, conversation: Conversation, thread_id: uuid.UUID | None) -> Conversation:
    """The thread of the lead a reply goes through: the lead itself unless a
    thread of the same group is named."""
    lead = primary_of(conversation)
    if thread_id is None or thread_id == lead.id:
        return lead
    thread = db.scalar(
        select(Conversation).where(Conversation.id == thread_id, Conversation.primary_conversation_id == lead.id)
    )
    if thread is None:
        raise HTTPException(status_code=404, detail="That thread is not part of this lead")
    return thread


# SQL predicates ---------------------------------------------------------------


def group_key():
    """The lead a row belongs to, in SQL: its primary, or itself. Message
    aggregates group by it so a lead's threads read as one."""
    return func.coalesce(Conversation.primary_conversation_id, Conversation.id)


def is_lead_row():
    """Rows that are leads (not absorbed by another one): what lists, the
    pipeline and reports count."""
    return Conversation.primary_conversation_id.is_(None)


def has_linked_threads():
    """Correlated: the row has at least one linked thread."""
    other = aliased(Conversation)
    return exists(select(other.id).where(other.primary_conversation_id == Conversation.id)).correlate(Conversation)


def has_human_thread():
    """Correlated: some thread linked to the row is answered by a person."""
    other = aliased(Conversation)
    return exists(
        select(other.id).where(other.primary_conversation_id == Conversation.id, other.mode == "human")
    ).correlate(Conversation)


@dataclass
class GroupStats:
    """What a list row says about the threads merged into its lead."""

    channels: list[str]
    linked_count: int = 0
    updated_at: datetime | None = None


def group_stats(db: Session, primaries: list[Conversation]) -> dict[uuid.UUID, GroupStats]:
    """Distinct channel codes (the primary's first), how many threads are linked
    and when they were last touched, for a page of leads in one extra query."""
    result = {row.id: GroupStats(channels=[row.channel]) for row in primaries}
    if not result:
        return result
    for primary_id, channel, updated_at in db.execute(
        select(Conversation.primary_conversation_id, Conversation.channel, Conversation.updated_at)
        .where(Conversation.primary_conversation_id.in_(list(result)))
        .order_by(Conversation.created_at, Conversation.id)
    ):
        stats = result[primary_id]
        stats.linked_count += 1
        if channel not in stats.channels:
            stats.channels.append(channel)
        if stats.updated_at is None or updated_at > stats.updated_at:
            stats.updated_at = updated_at
    return result


def thread_label(conversation: Conversation) -> str | None:
    """Who the thread talks to, the way the inbox names it: the phone on
    WhatsApp, the handle or name elsewhere."""
    from .contacts import phone_from_chat_id

    if conversation.channel in ("whatsapp", "whatsapp_cloud"):
        phone = phone_from_chat_id(conversation.external_chat_id)
        if phone:
            return f"+{phone}"
    return (conversation.contact_name or "").strip() or None
