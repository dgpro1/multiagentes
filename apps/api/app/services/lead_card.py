"""The lead card of the inbox: what a conversation looks like as a sales lead.

Shared by the client portal and the agency's panel, which read and edit the same
card from two doors. Both resolve the client their own way and hand it here;
every query is confined to that client.

The card only reads the stage and the budget: they change through the
pipeline routes (``services/pipeline.py``). What it writes is the responsible
member and the custom values. Choosing a responsible is only a label: it never
touches ``assignee_id`` or the AI/human mode, which decide who answers.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from ..models import Client, Contact, Conversation, PortalUser
from ..schemas_lead_card import LeadUpdate
from . import channel_accounts, lead_group
from .contacts import phone_from_chat_id
from .lead_fields import field_out, list_fields, merge_values, shown_values

_WHATSAPP_CHANNELS = ("whatsapp", "whatsapp_cloud")


def get_lead(db: Session, client: Client, conversation_id: uuid.UUID, *, act: bool = False) -> Conversation:
    """The lead of this client, or 404: a lead of another client (or agency) is
    never visible. A thread merged into another lead reads as that lead; writing
    through it (``act``) is refused, the change belongs to the lead."""
    row = db.scalar(
        select(Conversation.primary_conversation_id).where(
            Conversation.id == conversation_id,
            Conversation.client_id == client.id,
            Conversation.agency_id == client.agency_id,
        )
    )
    if row is not None:
        if act:
            raise HTTPException(status_code=409, detail=lead_group.ACT_ON_THE_LEAD)
        conversation_id = row
    conversation = db.scalar(
        select(Conversation)
        .options(
            selectinload(Conversation.contact).selectinload(Contact.tags),
            joinedload(Conversation.pipeline_stage),
            joinedload(Conversation.responsible),
        )
        .execution_options(populate_existing=True)
        .where(
            Conversation.id == conversation_id,
            Conversation.client_id == client.id,
            Conversation.agency_id == client.agency_id,
        )
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


def _responsible(client: Client, conversation: Conversation) -> dict:
    """The chosen member while they are still active at this client; otherwise
    the client's own responsible person."""
    member = conversation.responsible
    if member is not None and member.is_active and member.client_id == client.id:
        return {"id": member.id, "name": member.name.strip() or member.email, "is_default": False}
    return {"id": None, "name": client.owner_name, "is_default": True}


def _contact(conversation: Conversation) -> dict:
    contact = conversation.contact
    if contact is None:
        phone = phone_from_chat_id(conversation.external_chat_id) if conversation.channel in _WHATSAPP_CHANNELS else None
        return {
            "id": None, "name": None, "whatsapp_name": conversation.contact_name, "phone": phone,
            "email": None, "company": None, "blocked": False, "tags": [],
        }
    return {
        "id": contact.id,
        "name": contact.name.strip() or None,
        "whatsapp_name": conversation.contact_name,
        "phone": contact.phone,
        "email": contact.email,
        "company": contact.company,
        "blocked": contact.blocked_at is not None,
        "tags": [{"id": tag.id, "name": tag.name, "color": tag.color} for tag in contact.tags],
    }


def lead_card(db: Session, client: Client, conversation: Conversation) -> dict:
    fields = list_fields(db, client)
    stage = conversation.pipeline_stage
    group = lead_group.group_of(db, conversation)
    channel_accounts.annotate(db, group)
    return {
        "conversation_id": conversation.id,
        "number": conversation.number,
        "created_at": conversation.created_at,
        "channel": conversation.channel,
        "account_label": conversation.account_label,
        "linked_channels": [
            {
                "conversation_id": row.id,
                "channel": row.channel,
                "label": lead_group.thread_label(row),
                "account_label": row.account_label,
                "is_primary": row.id == conversation.id,
            }
            for row in group
        ],
        "stage": {"id": stage.id, "name": stage.name, "color": stage.color} if stage else None,
        "deal_value": float(conversation.deal_value) if conversation.deal_value is not None else None,
        "currency": client.currency or "USD",
        "responsible": _responsible(client, conversation),
        "owner_name": client.owner_name,
        "custom_values": shown_values(fields, conversation.custom_values),
        "fields": [field_out(field) for field in fields],
        "contact": _contact(conversation),
    }


def update_lead(db: Session, client: Client, conversation: Conversation, payload: LeadUpdate) -> None:
    """Apply the responsible and the custom values the caller sent, all or
    nothing: a bad value or a stranger's member changes nothing."""
    sent = payload.model_fields_set
    responsible_id = conversation.responsible_id
    if "responsible_id" in sent:
        if payload.responsible_id is None:
            responsible_id = None
        else:
            member = db.scalar(
                select(PortalUser.id).where(
                    PortalUser.id == payload.responsible_id,
                    PortalUser.client_id == client.id,
                    PortalUser.is_active.is_(True),
                )
            )
            if member is None:
                raise HTTPException(status_code=404, detail="That person is not part of this portal")
            responsible_id = member
    values = conversation.custom_values
    if payload.custom_values:
        values = merge_values(list_fields(db, client), conversation.custom_values, payload.custom_values)
    conversation.responsible_id = responsible_id
    conversation.custom_values = values
    db.commit()
