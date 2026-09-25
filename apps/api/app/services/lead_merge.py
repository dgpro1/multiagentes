"""Merging two leads into one, Kommo style.

A lead is a ``Conversation`` row. Merging does not delete the secondary: it
becomes a *linked thread* of the primary (``primary_conversation_id``). It keeps
its channel, its chat key and its messages, so whatever its channel delivers
keeps arriving on it, but from then on it is read through the primary and stops
being a lead of its own. The rules, in the order they are applied:

1. Both leads must belong to the client, be different, and not already be the
   same lead. A lead that was itself merged into another resolves to that one
   first; threads linked to the secondary move to the primary (no chains).
2. Budget: the primary keeps its ``deal_value``; only when it has none (or 0)
   does it take the secondary's.
3. Custom values: the primary's keys are never overwritten, the ones it lacks are
   added from the secondary. Responsible, assignee and team work like the budget.
4. Tags live on the contact and travel with the contact merge.
5. Contacts: two different contacts are merged (identities, conversations, tags
   and the company follow the primary's), one contact is adopted by the lead
   that had none.
6. The secondary and its threads point at the primary and take its state (mode,
   status, archive, assignee, team) so the lead behaves as one.
7. Messages and activity lines never move: the lead's timeline is the merge of
   its threads' messages.
8. The audit line ``entity_merged`` is recorded on the primary and keeps what
   the secondary had.
9. The secondary keeps only what its channel needs: its stage, budget, custom
   values and responsible are cleared, since they live on the primary now. Its
   number stays as an alias that resolves to the primary.

Shared by the client portal and the agency panel; every query is confined to the
client.
"""

from __future__ import annotations

import re
import uuid

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from ..models import Client, Contact, Conversation, now_utc
from ..schemas_lead_card import LeadMergeCandidateOut
from . import lead_group
from .contacts import merge_contacts, phone_from_chat_id
from .conversation_state import record_activity, set_status
from .text_search import folded_like

CANDIDATE_LIMIT = 20
PLAYGROUND = "playground"


def _lead_row(db: Session, client: Client, conversation_id: uuid.UUID) -> Conversation:
    conversation = db.scalar(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.client_id == client.id,
            Conversation.agency_id == client.agency_id,
        )
        .execution_options(populate_existing=True)
    )
    if conversation is None or conversation.channel == PLAYGROUND:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


def _contact(db: Session, client: Client, contact_id: uuid.UUID | None) -> Contact | None:
    if contact_id is None:
        return None
    return db.scalar(select(Contact).where(Contact.id == contact_id, Contact.client_id == client.id))


def _lead_name(conversation: Conversation, contact: Contact | None) -> str | None:
    name = (contact.name.strip() if contact else "") or (conversation.contact_name or "").strip()
    return name or None


def _price(conversation: Conversation) -> float | None:
    return float(conversation.deal_value) if conversation.deal_value is not None else None


def _channels(rows: list[Conversation]) -> list[str]:
    seen: list[str] = []
    for row in rows:
        if row.channel not in seen:
            seen.append(row.channel)
    return seen


def _snapshot(db: Session, client: Client, conversation: Conversation) -> dict:
    return {
        "number": conversation.number,
        "name": _lead_name(conversation, _contact(db, client, conversation.contact_id)),
        "price": _price(conversation),
        "currency": client.currency or "USD",
        "created_at": conversation.created_at.isoformat(),
        "channels": _channels(lead_group.group_of(db, conversation)),
    }


def merge_leads(
    db: Session, client: Client, primary_id: uuid.UUID, secondary_id: uuid.UUID, actor: str | None
) -> tuple[Conversation, int]:
    """Fold the secondary lead into the primary. Returns the primary and the
    number the secondary had (now an alias of the primary). Commits."""
    if primary_id == secondary_id:
        raise HTTPException(status_code=409, detail="Pick a different lead to merge")
    primary = _lead_row(db, client, primary_id)
    secondary = _lead_row(db, client, secondary_id)
    # A lead that was already merged into another one stands for that one.
    if lead_group.is_linked(primary):
        primary = _lead_row(db, client, primary.primary_conversation_id)
    if lead_group.is_linked(secondary):
        secondary = _lead_row(db, client, secondary.primary_conversation_id)
    if primary.id == secondary.id:
        raise HTTPException(status_code=409, detail="These conversations are already the same lead")
    # One order for every merge, so two of them never wait on each other's rows.
    for row_id in sorted({primary.id, secondary.id}, key=str):
        db.scalar(select(Conversation.id).where(Conversation.id == row_id).with_for_update())
    primary = _lead_row(db, client, primary.id)
    secondary = _lead_row(db, client, secondary.id)

    now = now_utc()
    secondary_is_open = secondary.status == "open"
    primary_threads = lead_group.linked_threads(db, primary)
    secondary_threads = lead_group.linked_threads(db, secondary)

    # 8. The audit keeps what the secondary had, taken before anything changes.
    primary_snapshot = _snapshot(db, client, primary)
    secondary_snapshot = _snapshot(db, client, secondary)
    secondary_snapshot["custom_fields"] = dict(secondary.custom_values or {})

    # 5. Contacts first: it moves conversations with bulk updates, so the rows
    # are read again before this merge changes anything on them.
    primary_contact = _contact(db, client, primary.contact_id)
    secondary_contact = _contact(db, client, secondary.contact_id)
    group = [primary, *primary_threads, secondary, *secondary_threads]
    if primary_contact is not None and secondary_contact is not None and primary_contact.id != secondary_contact.id:
        merge_contacts(db, primary_contact, secondary_contact)
        for row in group:
            db.refresh(row)
    survivor = primary_contact or secondary_contact
    final_contact_id = survivor.id if survivor else None

    # 2. Budget.
    if not primary.deal_value and secondary.deal_value and float(secondary.deal_value) > 0:
        primary.deal_value = secondary.deal_value
    # 3. Custom values fill in what the primary lacks; the rest is its own.
    merged_values = dict(primary.custom_values or {})
    for key, value in (secondary.custom_values or {}).items():
        merged_values.setdefault(key, value)
    primary.custom_values = merged_values
    if primary.responsible_id is None:
        primary.responsible_id = secondary.responsible_id
    if primary.assignee_id is None and secondary.assignee_id is not None:
        primary.assignee_id = secondary.assignee_id
        primary.assigned_at = secondary.assigned_at
    if primary.team_id is None:
        primary.team_id = secondary.team_id
    waits = [row.waiting_since for row in (primary, secondary) if row.waiting_since is not None]
    primary.waiting_since = min(waits) if waits else None
    if primary_contact is None and secondary_contact is not None:
        primary.contact_id = secondary_contact.id

    # 9. The secondary stops being a lead: its lead data lives on the primary.
    secondary.pipeline_stage_id = None
    secondary.deal_value = None
    secondary.custom_values = {}
    secondary.responsible_id = None

    # 6. Point every absorbed thread at the primary and make it behave as one
    # lead: same mode, status, archive, assignee, team and contact.
    for row in (secondary, *secondary_threads):
        row.primary_conversation_id = primary.id
    for row in (*primary_threads, secondary, *secondary_threads):
        row.mode = primary.mode
        row.taken_over_at = primary.taken_over_at
        row.assignee_id = primary.assignee_id
        row.assigned_at = primary.assigned_at
        row.team_id = primary.team_id
        row.phone_pause_until = None
        row.phone_resume_claimed_until = None
        row.status = primary.status
        row.status_changed_at = primary.status_changed_at
        row.resolved_at = primary.resolved_at
        row.archived_at = primary.archived_at
        if final_contact_id is not None:
            row.contact_id = final_contact_id
    if final_contact_id is not None:
        primary.contact_id = final_contact_id
    primary.updated_at = now

    details = {
        "primary_number": primary.number,
        "secondary_number": secondary.number,
        "primary": primary_snapshot,
        "secondary": secondary_snapshot,
    }
    record_activity(db, primary, "entity_merged", actor=actor, details=details)
    db.flush()
    # A waiting customer on the absorbed lead must not vanish with a resolved primary.
    if primary.status == "resolved" and primary.archived_at is None and secondary_is_open:
        set_status(db, primary, "open", actor=actor)
    secondary_number = secondary.number
    db.commit()
    db.refresh(primary)
    return primary, secondary_number


# Candidates ------------------------------------------------------------------


def _number_of(text: str) -> int | None:
    wanted = text.lstrip("#").strip()
    return int(wanted) if wanted.isdigit() and len(wanted) <= 9 else None


def merge_candidates(
    db: Session, client: Client, q: str | None, exclude: uuid.UUID | None, limit: int = CANDIDATE_LIMIT
) -> list[LeadMergeCandidateOut]:
    """Leads of this client the person could merge with the open one: only
    primaries, never the excluded lead nor its own threads. ``q`` matches the
    contact's name, phone or e-mail, accent- and case-insensitively, or the lead
    number with or without the leading ``#``. Without ``q`` the most recent."""
    limit = max(1, min(limit, CANDIDATE_LIMIT))
    query = (
        select(Conversation)
        .options(joinedload(Conversation.contact), joinedload(Conversation.pipeline_stage))
        .outerjoin(Contact, Contact.id == Conversation.contact_id)
        .where(
            Conversation.client_id == client.id,
            Conversation.agency_id == client.agency_id,
            Conversation.channel != PLAYGROUND,
            lead_group.is_lead_row(),
        )
    )
    if exclude is not None:
        excluded = _lead_row(db, client, exclude)
        query = query.where(Conversation.id.notin_(lead_group.group_ids(db, excluded)))
    term = (q or "").strip()
    if term:
        conditions = [
            folded_like(Contact.name, term),
            folded_like(Contact.email, term),
            folded_like(Conversation.contact_name, term),
        ]
        digits = re.sub(r"\D", "", term)
        if len(digits) >= 3:
            conditions.append(Contact.phone.contains(digits))
            conditions.append(Conversation.external_chat_id.contains(digits))
        number = _number_of(term)
        if number is not None:
            conditions.append(Conversation.number == number)
        query = query.where(or_(*conditions))
    rows = db.scalars(query.order_by(Conversation.updated_at.desc(), Conversation.number.desc()).limit(limit)).unique().all()
    stats = lead_group.group_stats(db, list(rows))
    out = []
    for row in rows:
        contact = row.contact
        stage = row.pipeline_stage
        out.append(
            LeadMergeCandidateOut(
                conversation_id=row.id,
                number=row.number,
                contact_name=_lead_name(row, contact),
                phone=(contact.phone if contact and contact.phone else None)
                or (phone_from_chat_id(row.external_chat_id) if row.channel in ("whatsapp", "whatsapp_cloud") else None),
                email=contact.email if contact and contact.email else None,
                channel=row.channel,
                channels=stats[row.id].channels,
                stage={"id": stage.id, "name": stage.name, "color": stage.color} if stage else None,
                deal_value=_price(row),
                created_at=row.created_at,
            )
        )
    return out
