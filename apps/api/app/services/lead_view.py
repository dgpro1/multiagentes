"""How a merged lead reads on the wire: one thread, several channels.

Both doors (the agency panel and the client portal) build the detail of a
conversation their own way, then hand it here to fold in the threads merged into
it: one chronological timeline whose messages say which thread and channel they
came from, the list of threads a reply can go through, and the aggregates the
lists show.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Conversation, Message
from ..schemas import ConversationDetail, ConversationOut, LinkedThreadOut, MessageOut
from . import channel_accounts, lead_group

_SOCIAL = ("instagram", "messenger")


def thread_window(conversation: Conversation, last_inbound_at: datetime | None) -> dict:
    """The reply window of one thread: WhatsApp Cloud and the social channels
    only allow a free-form answer for a while after the contact wrote."""
    if conversation.channel in _SOCIAL:
        from .social_policy import window_fields

        fields = window_fields(conversation)
        return {
            "reply_window_open": fields["human_reply_window_open"],
            "reply_window_until": fields["human_reply_window_until"],
            "reply_block_reason": fields["reply_block_reason"],
        }
    if conversation.channel == "whatsapp_cloud":
        from .whatsapp_templates import window_is_open, window_open_until

        return {
            "reply_window_open": window_is_open(last_inbound_at),
            "reply_window_until": window_open_until(last_inbound_at),
            "reply_block_reason": None,
        }
    return {"reply_window_open": True, "reply_window_until": None, "reply_block_reason": None}


def _is_inbound(message: Message) -> bool:
    return message.kind == "message" and message.sender_type == "visitor"


def with_group(db: Session, conversation: Conversation, detail: ConversationDetail) -> ConversationDetail:
    """Fold the threads merged into ``conversation`` (a lead) into its detail.

    ``detail`` is what the door built for the lead's own row. With nothing
    merged the timeline is the lead's and only the per-message ``channel`` is
    added; otherwise the messages of every thread come in one list, oldest
    first, and the lead shows as answered by a person while any thread is.
    """
    threads = lead_group.linked_threads(db, conversation)
    group = [conversation, *threads]
    channel_of = {row.id: row.channel for row in group}
    messages = [item.model_copy(update={"channel": conversation.channel}) for item in detail.messages]
    inbound: dict[uuid.UUID, datetime | None] = {conversation.id: None}
    for item in conversation.messages:
        if _is_inbound(item) and (inbound[conversation.id] is None or item.created_at > inbound[conversation.id]):
            inbound[conversation.id] = item.created_at
    latest: tuple[datetime, uuid.UUID] | None = None
    for item in conversation.messages:
        if _is_inbound(item) and not item.is_historical and (latest is None or item.created_at > latest[0]):
            latest = (item.created_at, conversation.id)
    if threads:
        rows = db.scalars(
            select(Message)
            .options(selectinload(Message.attachments))
            .where(Message.conversation_id.in_([row.id for row in threads]))
            .order_by(Message.created_at)
        ).all()
        for item in rows:
            messages.append(MessageOut.model_validate(item).model_copy(update={"channel": channel_of[item.conversation_id]}))
            if _is_inbound(item):
                if inbound.get(item.conversation_id) is None or item.created_at > inbound[item.conversation_id]:
                    inbound[item.conversation_id] = item.created_at
                if not item.is_historical and (latest is None or item.created_at > latest[0]):
                    latest = (item.created_at, item.conversation_id)
        messages.sort(key=lambda item: item.created_at)
    channel_accounts.annotate(db, group)
    stats = lead_group.group_stats(db, [conversation])[conversation.id]
    return detail.model_copy(
        update={
            "messages": messages,
            "mode": "human" if any(row.mode == "human" for row in group) else conversation.mode,
            "channels": stats.channels,
            "linked_count": stats.linked_count,
            "linked_threads": [
                LinkedThreadOut(
                    conversation_id=row.id,
                    channel=row.channel,
                    label=lead_group.thread_label(row),
                    account_label=row.account_label,
                    is_primary=row.id == conversation.id,
                    mode=row.mode,
                    last_inbound_at=inbound.get(row.id),
                    **thread_window(row, inbound.get(row.id)),
                )
                for row in group
            ],
            "reply_via_default": latest[1] if latest else conversation.id,
        }
    )


def list_item(
    item: ConversationOut, conversation: Conversation, stats: lead_group.GroupStats | None, *, group_human: bool = False
) -> ConversationOut:
    """Fold a lead's group into one list row: channels, how many threads were
    merged in, the lead answered by a person while any thread is, and the
    latest activity across the group."""
    update: dict = {}
    if group_human and item.mode != "human":
        update["mode"] = "human"
    if stats is not None:
        update["channels"] = stats.channels
        update["linked_count"] = stats.linked_count
        if stats.updated_at and stats.updated_at > item.updated_at:
            update["updated_at"] = stats.updated_at
    return item.model_copy(update=update) if update else item
