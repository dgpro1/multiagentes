"""Editing a contact and its tags, and the shape both doors answer with.

Shared by the client portal (``routers/portal.py``) and the agency's own routes
(``routers/lead_card.py``), which change the same rows from two doors. Both
resolve the client their own way and hand it here; every lookup is confined to
that client.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Client, Contact, ContactTag, Conversation, now_utc
from ..schemas import ContactOut, ContactTagOut, ContactUpdate
from ..schemas_lead_card import LeadContactAttach
from .conversation_state import record_activity, set_status
from .contacts import normalize_phone, rename_conversations, resolve_contact


def contact_stats():
    """Conversation counts and last activity per contact, as a subquery."""
    return (
        select(
            Conversation.contact_id.label("cid"),
            func.count(Conversation.id).label("total"),
            func.count(Conversation.id).filter(Conversation.status == "open").label("open"),
            func.max(Conversation.updated_at).label("last_activity_at"),
        )
        # A thread merged into another lead is not a case of its own.
        .where(Conversation.contact_id.is_not(None), Conversation.primary_conversation_id.is_(None))
        .group_by(Conversation.contact_id)
        .subquery()
    )


def contact_out(contact: Contact, stats) -> ContactOut:
    # Built by hand: the ORM object's ``conversations`` is the relationship,
    # not the count the portal wants.
    return ContactOut(
        id=contact.id,
        name=contact.name,
        phone=contact.phone,
        email=contact.email,
        company=contact.company,
        notes=contact.notes,
        created_at=contact.created_at,
        updated_at=contact.updated_at,
        conversation_count=int((stats.total if stats is not None else None) or 0),
        open_count=int((stats.open if stats is not None else None) or 0),
        last_activity_at=stats.last_activity_at if stats is not None else None,
        blocked_at=contact.blocked_at,
        tags=[ContactTagOut(id=tag.id, name=tag.name, color=tag.color) for tag in contact.tags],
    )


def contact_view(db: Session, contact: Contact) -> ContactOut:
    """The contact as the screens read it, with its conversation counts."""
    stats = contact_stats()
    row = db.execute(select(stats).where(stats.c.cid == contact.id)).first()
    return contact_out(contact, row)


def get_contact(db: Session, client: Client, contact_id: uuid.UUID) -> Contact:
    contact = db.scalar(select(Contact).where(Contact.id == contact_id, Contact.client_id == client.id))
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


def assert_phone_free(db: Session, client: Client, phone: str, *, except_id: uuid.UUID | None = None) -> None:
    query = select(Contact.id).where(Contact.client_id == client.id, Contact.phone == phone)
    if except_id:
        query = query.where(Contact.id != except_id)
    if db.scalar(query):
        raise HTTPException(status_code=409, detail="A contact with this phone number already exists")


def attach_contact(db: Session, client: Client, conversation: Conversation, payload: "LeadContactAttach") -> Contact:
    """Give a lead the person behind it.

    A lead born on a channel with nothing to identify the writer with has no
    contact, and without one the whole card is read-only: there is no person to
    name, tag or block. The operator is the one who knows who wrote, so the card
    asks them and this puts the two together.

    A phone that already belongs to somebody finds that person rather than
    making a second record of them, which is what a returning visitor looks
    like from the desk. A name on its own has nothing to match on, so it makes a
    new contact and the phone is what keeps the next lead off a duplicate.
    """
    phone = normalize_phone(payload.phone)
    name = payload.name.strip()[:180]
    if not phone and not name:
        raise HTTPException(status_code=422, detail="Enter a name or a phone number")
    if phone:
        contact = resolve_contact(db, client.id, phone=phone, name=name or None)
        if conversation.contact_id and conversation.contact_id != contact.id:
            # Two people behind one lead is a merge, not a retag: the lead
            # carries the whole history and the operator has to say which
            # person it belongs to.
            raise HTTPException(status_code=409, detail="This lead already has a contact")
    else:
        contact = Contact(client_id=client.id, name=name)
        db.add(contact)
        db.flush()
    conversation.contact_id = contact.id
    if name and not contact.name.strip():
        contact.name = name
    conversation.updated_at = now_utc()
    # The rename is a bulk update, and the lead only carries the contact in the
    # session so far: without this it would look for conversations under the old
    # contact and rename none, leaving the name the operator just typed in one
    # place and not the other.
    db.flush()
    rename_conversations(db, contact)
    db.commit()
    return contact


def update_contact(db: Session, client: Client, contact_id: uuid.UUID, payload: ContactUpdate) -> ContactOut:
    contact = get_contact(db, client, contact_id)
    if payload.phone is not None:
        phone = normalize_phone(payload.phone)
        if not phone:
            raise HTTPException(status_code=422, detail="Enter a phone number with its country code")
        assert_phone_free(db, client, phone, except_id=contact.id)
        contact.phone = phone
    if payload.name is not None:
        contact.name = payload.name.strip()
    if "email" in payload.model_fields_set:
        contact.email = payload.email or None
    if "company" in payload.model_fields_set:
        contact.company = (payload.company or "").strip() or None
    if payload.notes is not None:
        contact.notes = payload.notes.strip()
    contact.updated_at = now_utc()
    rename_conversations(db, contact)
    db.commit()
    db.refresh(contact)
    return contact_view(db, contact)


def block_contact(db: Session, client: Client, contact_id: uuid.UUID, blocked: bool, actor: str) -> ContactOut:
    """Block or unblock a contact, from whichever door reached it.

    Blocked, their messages are stored but never reach the agent or a phone,
    and their conversations leave the inboxes. Unblocking does not answer the
    backlog: the open conversation is resolved with a note, and the contact's
    next message opens a fresh one that the agent handles as usual.

    Both sides call this rather than each writing ``blocked_at``: the backlog is
    part of the decision, and a contact blocked from the portal and unblocked
    from the agency has to behave the same either way. ``actor`` is who pressed
    it, which only lands in the activity line of the conversations involved. """
    contact = get_contact(db, client, contact_id)
    open_ones = select(Conversation).where(Conversation.contact_id == contact.id, Conversation.status == "open")
    if blocked and contact.blocked_at is None:
        contact.blocked_at = now_utc()
        for conversation in db.scalars(open_ones).all():
            record_activity(db, conversation, "blocked", actor=actor)
    elif not blocked and contact.blocked_at is not None:
        contact.blocked_at = None
        for conversation in db.scalars(open_ones).all():
            set_status(db, conversation, "resolved", actor=actor)
            record_activity(db, conversation, "unblocked", actor=actor)
    db.commit()
    db.refresh(contact)
    return contact_view(db, contact)


def set_contact_tags(db: Session, client: Client, contact_id: uuid.UUID, tag_ids: list[uuid.UUID]) -> ContactOut:
    """Replace the contact's tags with the given set. Unknown ids, and tags of
    another client, are ignored rather than failing the whole change."""
    contact = get_contact(db, client, contact_id)
    wanted = set(tag_ids)
    tags = list(db.scalars(select(ContactTag).where(ContactTag.client_id == client.id, ContactTag.id.in_(wanted)))) if wanted else []
    contact.tags = tags
    db.commit()
    db.refresh(contact)
    return contact_view(db, contact)
