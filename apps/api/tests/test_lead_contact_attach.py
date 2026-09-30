"""Giving a lead the person behind it, from its own card.

A lead born on a channel with nothing to identify the writer arrives with no
contact, and a lead with no contact has nothing to work with: no name to edit,
no tags to put, nobody to block. These tests hold the door that puts the two
together, the rule that a phone already belonging to somebody links that person
instead of recording them twice, the refusal to swap a person who is already
there, and the permission behind both doors.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.models import Contact, Conversation
from conftest import TestingSession
from test_lead_card import Lead

FORBIDDEN = {"detail": "Your role cannot do this"}


@pytest.fixture
def lead(authenticated_client: TestClient) -> Lead:
    return Lead(authenticated_client)


def without_contact(lead: Lead) -> Lead:
    """The same lead with its contact taken away: what a lead that arrived with
    nothing to identify the writer looks like."""
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(lead.cid))
        conversation.contact_id = None
        db.commit()
    return lead


def with_contact(lead: Lead, name: str = "Marco Ruiz", phone: str = "5691100000") -> str:
    """A lead that arrived with a person behind it, and that person's id."""
    created = lead.admin.post(f"{lead.base}/contacts", json={"name": name, "phone": phone})
    assert created.status_code == 201, created.text
    contact_id = created.json()["id"]
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(lead.cid))
        conversation.contact_id = uuid.UUID(contact_id)
        db.commit()
    return contact_id


def test_a_lead_without_a_contact_has_nothing_to_tag(lead: Lead):
    """Why the card offers a door: without a contact there is no person to tag."""
    without_contact(lead)
    card = lead.agency.get(f"/api/conversations/{lead.cid}/lead")
    assert card.status_code == 200, card.text
    assert card.json()["contact"]["id"] is None


def test_adding_a_contact_answers_the_whole_card_back(lead: Lead):
    without_contact(lead)
    given = lead.agency.post(
        f"/api/conversations/{lead.cid}/contact", json={"name": "Lucia Vega", "phone": "+56 9 1111 2222"}
    )
    assert given.status_code == 200, given.text
    card = given.json()
    # Not the contact alone: the card comes back with the person in it, which is
    # what lets the panel unlock the tags and the block in one go.
    assert card["contact"]["name"] == "Lucia Vega"
    # Stored the way the inbound pipeline stores a number, so the next message
    # from that phone finds this contact instead of making a second one.
    assert card["contact"]["phone"] == "56911112222"


def test_a_phone_that_already_belongs_to_somebody_links_them(lead: Lead):
    """The returning visitor. The number is already a contact of this client, so
    the lead joins that person rather than becoming a second record of them."""
    contact_id = with_contact(lead)
    without_contact(lead)

    given = lead.agency.post(f"/api/conversations/{lead.cid}/contact", json={"name": "Marco Ruiz", "phone": "+5691100000"})
    assert given.status_code == 200, given.text
    assert given.json()["contact"]["id"] == contact_id

    with TestingSession() as db:
        people = db.query(Contact).filter(Contact.client_id == uuid.UUID(lead.id)).all()
        assert len(people) == 1, "the same person must not end up recorded twice"


def test_a_name_on_its_own_still_makes_a_contact(lead: Lead):
    """A channel with no number to match on. The operator can still say who
    wrote; nothing can keep the next lead off a duplicate."""
    without_contact(lead)
    given = lead.agency.post(f"/api/conversations/{lead.cid}/contact", json={"name": "Carla Medina"})
    assert given.status_code == 200, given.text
    assert given.json()["contact"]["name"] == "Carla Medina"


def test_nothing_to_go_by_is_refused(lead: Lead):
    without_contact(lead)
    refused = lead.agency.post(f"/api/conversations/{lead.cid}/contact", json={"name": "   ", "phone": ""})
    assert refused.status_code == 422, refused.text


def test_a_lead_that_already_has_a_contact_is_left_alone(lead: Lead):
    """Putting a second person behind a lead is a merge, which the card offers on
    its own; this door is not where that decision is made."""
    with_contact(lead)
    before = lead.agency.get(f"/api/conversations/{lead.cid}/lead").json()["contact"]
    refused = lead.agency.post(f"/api/conversations/{lead.cid}/contact", json={"name": "Otra", "phone": "5691199999"})
    assert refused.status_code == 409, refused.text
    assert lead.agency.get(f"/api/conversations/{lead.cid}/lead").json()["contact"] == before


def test_the_name_they_typed_shows_up_everywhere(lead: Lead):
    """The lead and the contact are one person's name now. The title the inbox
    reads follows at once, rather than on the poll eight seconds later."""
    without_contact(lead)
    given = lead.agency.post(f"/api/conversations/{lead.cid}/contact", json={"name": "Lucia Vega", "phone": "5691144444"})
    assert given.status_code == 200, given.text
    assert given.json()["contact"]["name"] == "Lucia Vega"

    # The thread header and the list read the conversation, not the card.
    thread = lead.agency.get(f"/api/conversations/{lead.cid}")
    assert thread.status_code == 200, thread.text
    assert thread.json()["title"] == "Lucia Vega"


def test_the_portal_has_the_same_door(lead: Lead):
    without_contact(lead)
    given = lead.admin.post(f"{lead.base}/conversations/{lead.cid}/contact", json={"name": "Lucia Vega", "phone": "5691133333"})
    assert given.status_code == 200, given.text
    assert given.json()["contact"]["name"] == "Lucia Vega"


def test_the_portal_door_answers_to_its_permission(lead: Lead):
    """A member with no contacts permission is shown the read-only card, so the
    card does not offer the door either."""
    without_contact(lead)
    member = lead.member("juan", "agent")
    assert member.get(f"{lead.base}/conversations/{lead.cid}/lead").status_code == 200
    refused = member.post(f"{lead.base}/conversations/{lead.cid}/contact", json={"name": "Lucia"})
    assert refused.status_code == 403, refused.text
    assert refused.json() == FORBIDDEN
