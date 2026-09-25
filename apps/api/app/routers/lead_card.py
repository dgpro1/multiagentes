"""The lead card, agency side: a conversation seen as a sales lead, the contact
behind it and the custom fields a client defines for it. The client portal
serves the same card through its own routes (``routers/portal.py``); both hand
the client to ``services/lead_card.py``, ``services/lead_fields.py`` and
``services/contact_edit.py``.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api_scopes import CONTACTS_MANAGE, INBOX_MANAGE, INBOX_READ, LEAD_FIELDS_MANAGE, LEAD_FIELDS_READ
from ..database import get_db
from ..deps import confined_client_id, get_current_user, require
from ..models import Client, Conversation, User
from ..schemas import ContactOut, ContactTagsSet, ContactUpdate
from ..schemas_lead_card import (
    LeadCardOut,
    LeadFieldCreate,
    LeadFieldOut,
    LeadFieldUpdate,
    LeadMergeCandidateOut,
    LeadMergeOut,
    LeadMergeRequest,
    LeadUpdate,
)
from ..services import contact_edit
from ..services import lead_merge as lead_merge_service
from ..services import lead_card as lead_card_service
from ..services import lead_fields as lead_fields_service

router = APIRouter(tags=["Lead card"])


def _client(db: Session, user: User, client_id: uuid.UUID) -> Client:
    query = select(Client).where(Client.id == client_id, Client.agency_id == user.agency_id)
    # A client's portal admin reaches only its own client; see PortalActor.
    if (only_client := confined_client_id(user)) is not None:
        query = query.where(Client.id == only_client)
    client = db.scalar(query)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


def _conversation_client(db: Session, user: User, conversation_id: uuid.UUID) -> Client:
    """The client that owns the conversation, resolved inside the caller's agency."""
    client_id = db.scalar(
        select(Conversation.client_id).where(Conversation.id == conversation_id, Conversation.agency_id == user.agency_id)
    )
    if client_id is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        return _client(db, user, client_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Conversation not found") from None


@router.get("/conversations/{conversation_id}/lead", response_model=LeadCardOut, dependencies=[Depends(require(INBOX_READ))])
def get_lead(conversation_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    client = _conversation_client(db, user, conversation_id)
    return lead_card_service.lead_card(db, client, lead_card_service.get_lead(db, client, conversation_id))


@router.patch("/conversations/{conversation_id}/lead", response_model=LeadCardOut, dependencies=[Depends(require(INBOX_MANAGE))])
def update_lead(
    conversation_id: uuid.UUID, payload: LeadUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    client = _conversation_client(db, user, conversation_id)
    conversation = lead_card_service.get_lead(db, client, conversation_id, act=True)
    lead_card_service.update_lead(db, client, conversation, payload)
    return lead_card_service.lead_card(db, client, lead_card_service.get_lead(db, client, conversation_id))


@router.get(
    "/clients/{client_id}/leads/merge-candidates",
    response_model=list[LeadMergeCandidateOut],
    dependencies=[Depends(require(INBOX_READ))],
)
def merge_candidates(
    client_id: uuid.UUID,
    q: str | None = Query(default=None, max_length=120),
    exclude: uuid.UUID | None = None,
    limit: int = Query(default=20, ge=1, le=20),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Leads of the client that can be merged with ``exclude``: primaries only,
    found by contact name, phone, e-mail or number."""
    return lead_merge_service.merge_candidates(db, _client(db, user, client_id), q, exclude, limit)


@router.post(
    "/clients/{client_id}/leads/merge", response_model=LeadMergeOut, dependencies=[Depends(require(CONTACTS_MANAGE))]
)
def merge_leads(
    client_id: uuid.UUID, payload: LeadMergeRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Fold the secondary lead into the primary one. Final: the secondary stays
    as a linked thread of the primary and its number becomes an alias."""
    client = _client(db, user, client_id)
    primary, secondary_number = lead_merge_service.merge_leads(
        db, client, payload.primary_conversation_id, payload.secondary_conversation_id, user.name
    )
    return {"primary": lead_card_service.lead_card(db, client, lead_card_service.get_lead(db, client, primary.id)),
            "secondary_number": secondary_number}


@router.patch(
    "/clients/{client_id}/contacts/{contact_id}", response_model=ContactOut, dependencies=[Depends(require(CONTACTS_MANAGE))]
)
def update_contact(
    client_id: uuid.UUID, contact_id: uuid.UUID, payload: ContactUpdate,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    return contact_edit.update_contact(db, _client(db, user, client_id), contact_id, payload)


@router.put(
    "/clients/{client_id}/contacts/{contact_id}/tags", response_model=ContactOut, dependencies=[Depends(require(CONTACTS_MANAGE))]
)
def set_contact_tags(
    client_id: uuid.UUID, contact_id: uuid.UUID, payload: ContactTagsSet,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    return contact_edit.set_contact_tags(db, _client(db, user, client_id), contact_id, payload.tag_ids)


@router.get("/clients/{client_id}/lead-fields", response_model=list[LeadFieldOut], dependencies=[Depends(require(LEAD_FIELDS_READ))])
def client_lead_fields(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [lead_fields_service.field_out(row) for row in lead_fields_service.list_fields(db, _client(db, user, client_id))]


@router.post(
    "/clients/{client_id}/lead-fields", response_model=LeadFieldOut, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(LEAD_FIELDS_MANAGE))],
)
def client_create_lead_field(
    client_id: uuid.UUID, payload: LeadFieldCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return lead_fields_service.field_out(lead_fields_service.create_field(db, _client(db, user, client_id), payload))


@router.patch(
    "/clients/{client_id}/lead-fields/{field_id}", response_model=LeadFieldOut, dependencies=[Depends(require(LEAD_FIELDS_MANAGE))]
)
def client_update_lead_field(
    client_id: uuid.UUID, field_id: uuid.UUID, payload: LeadFieldUpdate,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    return lead_fields_service.field_out(lead_fields_service.update_field(db, _client(db, user, client_id), field_id, payload))


@router.delete(
    "/clients/{client_id}/lead-fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require(LEAD_FIELDS_MANAGE))],
)
def client_delete_lead_field(
    client_id: uuid.UUID, field_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    lead_fields_service.delete_field(db, _client(db, user, client_id), field_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
