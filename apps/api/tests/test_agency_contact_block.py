"""Blocking a contact from the agency's own inbox.

The portal has blocked a contact for a long while; the agency could not, even
though the lead card is the same card in both places. Both doors now answer the
same call (``services.contact_edit.block_contact``), so the rule they apply has
to be the same rule: blocked, the contact leaves every inbox, and unblocking
closes the open case without answering the backlog.

What is checked here is the agency's door and the cross-door agreement, not the
message pipeline, which ``test_lifecycle.py`` covers from the portal's side.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import TestingSession, customer_conversation


def _lead_with_a_contact(client: TestClient) -> tuple[dict, dict]:
    """A lead whose conversation belongs to a contact, which is what the lead
    card's menu acts on. The contact hangs off the conversation, the way the
    inbound pipeline leaves it."""
    customer = client.post("/api/clients", json={"name": "Block Co", "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post(
        "/api/agents",
        json={"client_id": customer["id"], "provider": "openrouter", "model": "gpt-4.1-mini",
              "name": "Vera", "instructions": "", "personality": "", "is_active": True},
    ).json()
    conversation = customer_conversation(client, agent["id"])

    from app.models import Contact, Conversation, Message

    with TestingSession() as db:
        contact = db.query(Contact).filter(Contact.client_id == customer["id"]).first()
        if contact is None:
            contact = Contact(client_id=uuid.UUID(customer["id"]), name="Ana", phone="+573001112233")
            db.add(contact)
            db.flush()
        row = db.get(Conversation, uuid.UUID(conversation["id"]))
        row.contact_id = contact.id
        db.add(Message(conversation_id=row.id, role="user", content="hola",
                       sender_type="visitor", sender_name="Ana"))
        db.commit()
        contact_id = contact.id
    conversation["contact_id"] = str(contact_id)
    return customer, conversation


def test_the_agency_can_block_a_contact_from_the_lead_card(authenticated_client: TestClient):
    client = authenticated_client
    customer, conversation = _lead_with_a_contact(client)
    base = f"/api/clients/{customer['id']}/contacts/{conversation['contact_id']}"

    assert client.get("/api/conversations/inbox").json(), "the lead starts in the inbox"
    assert client.post(f"{base}/block", json={"blocked": True}).json()["blocked_at"]

    # Blocked, the lead leaves the agency's own inbox.
    assert [row["id"] for row in client.get("/api/conversations/inbox").json()] == []

    # Unblocking closes the open case rather than leaving it in the list.
    unblocked = client.post(f"{base}/block", json={"blocked": False})
    assert unblocked.status_code == 200, unblocked.text
    assert unblocked.json()["blocked_at"] is None


def test_both_doors_answer_with_the_same_contact(authenticated_client: TestClient):
    """A contact blocked from the portal reads as blocked in the agency, and the
    other way round. Two rules would be two sources of truth about the same row."""
    client = authenticated_client
    customer, conversation = _lead_with_a_contact(client)
    portal_base = f"/api/portal/{customer['portal_slug']}"
    agency_base = f"/api/clients/{customer['id']}/contacts/{conversation['contact_id']}"

    member = client.post(f"/api/clients/{customer['id']}/portal-users",
                         json={"name": "Ana", "email": f"ana@{customer['portal_slug']}.com", "password": "secure-portal", "role": "admin"})
    assert member.status_code == 201, member.text
    assert client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_enabled": True}).status_code == 200
    session = TestClient(client.app)
    assert session.post(f"{portal_base}/login", json={"email": f"ana@{customer['portal_slug']}.com", "password": "secure-portal"}).status_code == 200

    session.post(f"{portal_base}/contacts/{conversation['contact_id']}/block", json={"blocked": True})
    # The agency sees it, and does not have to ask the portal: both doors write
    # the same column. The v1 contact reader deliberately leaves blocked_at out,
    # so the check reads it back the way the panel does, through the block door.
    from_agency = client.post(f"{agency_base}/block", json={"blocked": True})
    assert from_agency.status_code == 200, from_agency.text
    assert from_agency.json()["blocked_at"], "the agency must see what the portal blocked"

    assert client.post(f"{agency_base}/block", json={"blocked": False}).json()["blocked_at"] is None
    # And the portal agrees without asking the agency.
    from_portal = next(row for row in session.get(f"{portal_base}/contacts").json() if row["id"] == conversation["contact_id"])
    assert from_portal["blocked_at"] is None


def test_another_clients_contact_is_not_reachable_from_this_one(authenticated_client: TestClient):
    """The door is per client: a contact id from another client is not found,
    which is what keeps an agency of several clients from reaching sideways."""
    client = authenticated_client
    customer, conversation = _lead_with_a_contact(client)
    stranger = client.post("/api/clients", json={"name": "Otro", "is_active": True}).json()
    response = client.post(f"/api/clients/{stranger['id']}/contacts/{conversation['contact_id']}/block", json={"blocked": True})
    assert response.status_code == 404, response.text


def test_a_token_with_only_reads_cannot_block(authenticated_client: TestClient):
    """The door is guarded like every other write on contacts. A secret holding
    only reads is refused rather than silently allowed, which is what
    ``require(CONTACTS_MANAGE)`` in the decorator is for."""
    client = authenticated_client
    customer, conversation = _lead_with_a_contact(client)
    integration = client.post("/api/integrations", json={"name": "Solo lectura", "preset": "read_only"}).json()
    secret = client.post(f"/api/integrations/{integration['id']}/tokens", json={"expires_in_days": 30}).json()["token"]
    base = f"/api/clients/{customer['id']}/contacts/{conversation['contact_id']}/block"

    refused = client.post(base, json={"blocked": True}, headers={"X-API-Key": secret})
    assert refused.status_code == 403, refused.text
    # And the contact is untouched: reading it back through the agency door is
    # how the panel sees it, and the v1 reader leaves blocked_at out on purpose.
    assert client.get(f"/api/v1/clients/{customer['id']}/contacts/{conversation['contact_id']}").status_code == 200
    still = client.post(base, json={"blocked": False})
    assert still.status_code == 200, still.text
