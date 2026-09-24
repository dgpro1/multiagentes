"""The lead card of the inbox: what a conversation looks like as a sales lead.

Served by two doors over the same rows, the client portal (behind the ``inbox``
portal function) and the agency's panel. These tests hold the card's shape, the
responsible-versus-assignee rule, the custom fields and their value checks, the
permission of the portal's field editor, the ownership boundary, and the client
details (responsible person, currency) the card leans on.
"""

import re
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.models import Contact, ContactTag, Conversation, LeadField, PortalUser
from app.services.client_details import CURRENCIES
from conftest import TestingSession, customer_conversation, login_legacy_owner

NO_FEATURE = {"detail": "This feature is not enabled for this portal"}
FORBIDDEN = {"detail": "Your role cannot do this"}
ZERO = "00000000-0000-0000-0000-000000000000"
CARD_KEYS = {
    "conversation_id", "number", "channel", "account_label", "stage", "deal_value", "currency", "responsible",
    "owner_name", "custom_values", "fields", "contact",
}
CONTACT_KEYS = {"id", "name", "whatsapp_name", "phone", "email", "company", "blocked", "tags"}


class Lead:
    """A client with an open portal (its admin signed in), one agent and one customer conversation."""

    def __init__(self, client: TestClient, name: str = "Lead Co"):
        self.agency = client
        self.members: dict[str, str] = {}
        self.customer = client.post("/api/clients", json={"name": name, "is_active": True}).json()
        self.id = self.customer["id"]
        self.slug = self.customer["portal_slug"]
        self.base = f"/api/portal/{self.slug}"
        client.put("/api/providers/openrouter", json={"api_key": "secret"})
        agent = client.post(
            "/api/agents",
            json={"client_id": self.id, "provider": "openrouter", "model": "gpt-4.1-mini", "name": "Vera", "instructions": "", "personality": "", "is_active": True},
        ).json()
        self.conversation = customer_conversation(client, agent["id"])
        self.cid = self.conversation["id"]
        self.admin = self.member("ana", "admin")

    def member(self, name: str, role: str = "agent") -> TestClient:
        """A person of the portal in a session of their own."""
        body = {"name": name.title(), "email": f"{name}@{self.slug}.com", "password": "secure-portal", "role": role}
        created = self.agency.post(f"/api/clients/{self.id}/portal-users", json=body)
        assert created.status_code == 201, created.text
        self.members[name] = created.json()["id"]
        # A portal opens once it has someone to sign in.
        assert self.agency.patch(f"/api/clients/{self.id}/portal", json={"portal_enabled": True}).status_code == 200
        session = TestClient(self.agency.app)
        assert session.post(f"{self.base}/login", json={"email": body["email"], "password": "secure-portal"}).status_code == 200
        return session


@pytest.fixture
def lead(authenticated_client: TestClient) -> Lead:
    return Lead(authenticated_client)


def _field(session: TestClient, base: str, label: str, type_: str, **extra) -> dict:
    created = session.post(f"{base}/lead-fields", json={"label": label, "type": type_, **extra})
    assert created.status_code == 201, created.text
    return created.json()


def _patch(session: TestClient, base: str, cid: str, body: dict):
    return session.patch(f"{base}/conversations/{cid}/lead", json=body)


# --- the card, on both doors ---------------------------------------------------------------


def test_the_card_has_the_same_shape_on_both_doors(lead: Lead):
    portal = lead.admin.get(f"{lead.base}/conversations/{lead.cid}/lead")
    assert portal.status_code == 200, portal.text
    card = portal.json()
    assert set(card) == CARD_KEYS and set(card["contact"]) == CONTACT_KEYS
    assert card["conversation_id"] == lead.cid and card["number"] >= 1 and card["channel"] == "widget"
    assert card["stage"] is None and card["deal_value"] is None and card["currency"] == "USD"
    assert card["responsible"] == {"id": None, "name": None, "is_default": True}
    assert card["owner_name"] is None and card["custom_values"] == {} and card["fields"] == []
    assert card["contact"]["id"] is None and card["contact"]["tags"] == [] and card["contact"]["blocked"] is False

    agency = lead.agency.get(f"/api/conversations/{lead.cid}/lead")
    assert agency.status_code == 200, agency.text
    assert agency.json() == card


def test_the_card_reads_stage_budget_currency_contact_and_tags(lead: Lead):
    lead.agency.patch(f"/api/clients/{lead.id}", json={"owner_name": "Dr. Ruiz", "currency": "cop"})
    stage = lead.agency.post(f"/api/clients/{lead.id}/pipeline/stages", json={"name": "Negotiating", "color": "#00a67d"}).json()
    assert lead.admin.patch(
        f"{lead.base}/conversations/{lead.cid}/pipeline", json={"pipeline_stage_id": stage["id"], "deal_value": 1500000.5}
    ).status_code == 200
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(lead.cid))
        contact = Contact(client_id=conversation.client_id, name="Rita Gómez", phone="573001112233", email="rita@example.com", company="Acme")
        contact.tags = [ContactTag(client_id=conversation.client_id, name="VIP", color="#ef4444")]
        db.add(contact)
        db.flush()
        conversation.contact_id = contact.id
        conversation.contact_name = "Rita W."
        db.commit()

    card = lead.admin.get(f"{lead.base}/conversations/{lead.cid}/lead").json()
    assert card["stage"] == {"id": stage["id"], "name": "Negotiating", "color": "#00a67d"}
    assert card["deal_value"] == 1500000.5 and card["currency"] == "COP" and card["owner_name"] == "Dr. Ruiz"
    contact = card["contact"]
    assert (contact["name"], contact["whatsapp_name"], contact["phone"], contact["email"], contact["company"]) == (
        "Rita Gómez", "Rita W.", "573001112233", "rita@example.com", "Acme",
    )
    assert [(tag["name"], tag["color"]) for tag in contact["tags"]] == [("VIP", "#ef4444")]
    assert lead.agency.get(f"/api/conversations/{lead.cid}/lead").json() == card

    # The card only reads the stage and the budget: they move through the pipeline routes.
    assert lead.admin.get(f"{lead.base}/pipeline/board").json()["currency"] == "COP"


def test_a_conversation_without_a_contact_derives_the_phone_from_the_chat_id(lead: Lead):
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(lead.cid))
        conversation.channel = "whatsapp_cloud"
        conversation.external_chat_id = "573001112233"
        db.commit()
    contact = lead.admin.get(f"{lead.base}/conversations/{lead.cid}/lead").json()["contact"]
    assert contact["id"] is None and contact["phone"] == "573001112233"


# --- responsible ---------------------------------------------------------------------------


def test_the_responsible_defaults_to_the_owner_then_the_chosen_member(lead: Lead):
    lead.agency.patch(f"/api/clients/{lead.id}", json={"owner_name": "Dr. Ruiz"})
    url = f"{lead.base}/conversations/{lead.cid}/lead"
    assert lead.admin.get(url).json()["responsible"] == {"id": None, "name": "Dr. Ruiz", "is_default": True}

    lead.member("beto")
    beto = lead.members["beto"]
    before = lead.admin.get(f"{lead.base}/conversations/{lead.cid}").json()
    chosen = _patch(lead.admin, lead.base, lead.cid, {"responsible_id": beto})
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["responsible"] == {"id": beto, "name": "Beto", "is_default": False}
    assert chosen.json()["owner_name"] == "Dr. Ruiz"

    # Choosing a responsible is only a label: who answers does not change.
    after = lead.admin.get(f"{lead.base}/conversations/{lead.cid}").json()
    assert (after["mode"], after["assignee_id"]) == (before["mode"], before["assignee_id"])
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(lead.cid))
        assert conversation.assignee_id is None and conversation.mode == before["mode"]

    # Clearing returns the default; a partial patch without the key leaves it alone.
    assert _patch(lead.admin, lead.base, lead.cid, {"custom_values": {}}).json()["responsible"]["id"] == beto
    cleared = _patch(lead.admin, lead.base, lead.cid, {"responsible_id": None})
    assert cleared.json()["responsible"] == {"id": None, "name": "Dr. Ruiz", "is_default": True}


def test_an_inactive_responsible_falls_back_to_the_owner(lead: Lead):
    lead.agency.patch(f"/api/clients/{lead.id}", json={"owner_name": "Dr. Ruiz"})
    lead.member("beto")
    beto = lead.members["beto"]
    assert _patch(lead.admin, lead.base, lead.cid, {"responsible_id": beto}).status_code == 200
    with TestingSession() as db:
        db.get(PortalUser, uuid.UUID(beto)).is_active = False
        db.commit()
    assert lead.agency.get(f"/api/conversations/{lead.cid}/lead").json()["responsible"]["is_default"] is True


def test_the_responsible_must_be_an_active_member_of_the_same_client(lead: Lead, authenticated_client: TestClient):
    other = Lead(authenticated_client, "Other Co")
    other_member = other.members["ana"]
    refused = _patch(lead.admin, lead.base, lead.cid, {"responsible_id": other_member})
    assert refused.status_code == 404
    assert _patch(lead.admin, lead.base, lead.cid, {"responsible_id": ZERO}).status_code == 404
    lead.member("beto")
    with TestingSession() as db:
        db.get(PortalUser, uuid.UUID(lead.members["beto"])).is_active = False
        db.commit()
    assert _patch(lead.admin, lead.base, lead.cid, {"responsible_id": lead.members["beto"]}).status_code == 404
    assert lead.agency.patch(f"/api/conversations/{lead.cid}/lead", json={"responsible_id": other_member}).status_code == 404
    assert lead.agency.get(f"/api/conversations/{lead.cid}/lead").json()["responsible"]["id"] is None


# --- custom fields -------------------------------------------------------------------------


def test_field_keys_are_slugs_and_unique_and_never_change(lead: Lead):
    first = _field(lead.admin, lead.base, "  Presupuesto Máximo! ", "text")
    assert first["key"] == "presupuesto_maximo" and first["label"] == "Presupuesto Máximo!"
    assert _field(lead.admin, lead.base, "Presupuesto maximo", "number")["key"] == "presupuesto_maximo_2"
    assert _field(lead.admin, lead.base, "Presupuesto maximo", "date")["key"] == "presupuesto_maximo_3"
    assert _field(lead.admin, lead.base, "???", "checkbox")["key"] == "field"

    renamed = lead.admin.patch(f"{lead.base}/lead-fields/{first['id']}", json={"label": "Tope", "position": 9})
    assert renamed.status_code == 200, renamed.text
    assert (renamed.json()["key"], renamed.json()["type"], renamed.json()["label"], renamed.json()["position"]) == ("presupuesto_maximo", "text", "Tope", 9)
    # The key and the type are not editable: they are ignored, never applied.
    ignored = lead.admin.patch(f"{lead.base}/lead-fields/{first['id']}", json={"key": "hacked", "type": "number"})
    assert ignored.status_code == 200 and (ignored.json()["key"], ignored.json()["type"]) == ("presupuesto_maximo", "text")

    listed = lead.admin.get(f"{lead.base}/lead-fields").json()
    assert [row["position"] for row in listed] == sorted(row["position"] for row in listed)
    assert set(listed[0]) == {"id", "key", "label", "type", "options", "position"}
    assert lead.agency.get(f"/api/clients/{lead.id}/lead-fields").json() == listed


def test_only_a_select_has_options_and_they_are_checked(lead: Lead):
    assert lead.admin.post(f"{lead.base}/lead-fields", json={"label": "Notes", "type": "text", "options": ["a"]}).status_code == 422
    assert lead.admin.post(f"{lead.base}/lead-fields", json={"label": "Bad", "type": "weird"}).status_code == 422
    assert lead.admin.post(f"{lead.base}/lead-fields", json={"label": "  ", "type": "text"}).status_code == 422
    for options in (["a", "a"], ["a", "  "], ["x" * 61], [str(n) for n in range(31)]):
        assert lead.admin.post(f"{lead.base}/lead-fields", json={"label": "Pick", "type": "select", "options": options}).status_code == 422, options

    pick = _field(lead.admin, lead.base, "Interest", "select", options=[" Hot ", "Cold"])
    assert pick["options"] == ["Hot", "Cold"]
    assert lead.admin.patch(f"{lead.base}/lead-fields/{pick['id']}", json={"options": ["Hot", "Cold", "Warm"]}).json()["options"] == ["Hot", "Cold", "Warm"]
    assert lead.admin.patch(f"{lead.base}/lead-fields/{pick['id']}", json={"options": ["A", "A"]}).status_code == 422
    plain = _field(lead.admin, lead.base, "Plain", "text")
    assert lead.admin.patch(f"{lead.base}/lead-fields/{plain['id']}", json={"options": ["a"]}).status_code == 422


def test_a_client_has_at_most_thirty_fields(lead: Lead):
    for n in range(30):
        _field(lead.admin, lead.base, f"Field {n}", "text")
    over = lead.admin.post(f"{lead.base}/lead-fields", json={"label": "One more", "type": "text"})
    assert over.status_code == 409, over.text
    assert lead.agency.post(f"/api/clients/{lead.id}/lead-fields", json={"label": "One more", "type": "text"}).status_code == 409


def test_custom_values_are_checked_per_type(lead: Lead):
    text = _field(lead.admin, lead.base, "Nickname", "text")
    number = _field(lead.admin, lead.base, "Seats", "number")
    day = _field(lead.admin, lead.base, "Visit", "date")
    pick = _field(lead.admin, lead.base, "Interest", "select", options=["Hot", "Cold"])
    flag = _field(lead.admin, lead.base, "Vip", "checkbox")

    good = _patch(lead.admin, lead.base, lead.cid, {"custom_values": {
        "nickname": "Ri", "seats": 2.5, "visit": "2026-03-01", "interest": "Hot", "vip": True,
    }})
    assert good.status_code == 200, good.text
    assert good.json()["custom_values"] == {"nickname": "Ri", "seats": 2.5, "visit": "2026-03-01", "interest": "Hot", "vip": True}
    assert [row["key"] for row in good.json()["fields"]] == ["nickname", "seats", "visit", "interest", "vip"]

    def bad(key: str, value):
        response = _patch(lead.admin, lead.base, lead.cid, {"custom_values": {key: value}})
        assert response.status_code == 422, (key, value, response.status_code, response.text)

    for key, value in (
        ("nickname", 5), ("nickname", "x" * 501), ("nickname", True),
        ("seats", "2"), ("seats", True),
        ("visit", "2026-13-40"), ("visit", "01/03/2026"), ("visit", "20260301"), ("visit", 20260301),
        ("interest", "Lukewarm"), ("interest", 1),
        ("vip", "yes"), ("vip", 1),
        ("ghost", "x"),
    ):
        bad(key, value)
    # Nothing was written by the refused calls, and one bad key spoils the whole call.
    assert _patch(lead.admin, lead.base, lead.cid, {"custom_values": {"nickname": "New", "seats": "x"}}).status_code == 422
    assert lead.admin.get(f"{lead.base}/conversations/{lead.cid}/lead").json()["custom_values"]["nickname"] == "Ri"

    # A partial patch keeps the other keys; null clears one.
    cleared = _patch(lead.admin, lead.base, lead.cid, {"custom_values": {"nickname": None, "vip": False}})
    assert cleared.json()["custom_values"] == {"seats": 2.5, "visit": "2026-03-01", "interest": "Hot", "vip": False}
    assert lead.agency.get(f"/api/conversations/{lead.cid}/lead").json() == cleared.json()
    assert (text["type"], number["type"], day["type"], pick["type"], flag["type"]) == ("text", "number", "date", "select", "checkbox")


def test_the_agency_fills_a_lead_and_the_portal_sees_it(lead: Lead):
    _field(lead.agency, f"/api/clients/{lead.id}", "Budget note", "text")
    changed = lead.agency.patch(f"/api/conversations/{lead.cid}/lead", json={"custom_values": {"budget_note": "flexible"}})
    assert changed.status_code == 200, changed.text
    assert lead.admin.get(f"{lead.base}/conversations/{lead.cid}/lead").json()["custom_values"] == {"budget_note": "flexible"}


def test_deleting_a_field_leaves_its_values_as_orphans_nobody_sees(lead: Lead):
    note = _field(lead.admin, lead.base, "Note", "text")
    _patch(lead.admin, lead.base, lead.cid, {"custom_values": {"note": "hello"}})
    assert lead.admin.delete(f"{lead.base}/lead-fields/{note['id']}").status_code == 204
    card = lead.admin.get(f"{lead.base}/conversations/{lead.cid}/lead").json()
    assert card["custom_values"] == {} and card["fields"] == []
    with TestingSession() as db:
        assert db.get(Conversation, uuid.UUID(lead.cid)).custom_values == {"note": "hello"}
    assert _patch(lead.admin, lead.base, lead.cid, {"custom_values": {"note": "again"}}).status_code == 422
    # A new field that reuses the key does not surface a value of another shape.
    _field(lead.admin, lead.base, "Note", "checkbox")
    assert lead.admin.get(f"{lead.base}/conversations/{lead.cid}/lead").json()["custom_values"] == {}
    assert lead.admin.delete(f"{lead.base}/lead-fields/{note['id']}").status_code == 404


def test_the_agency_manages_the_fields_too(lead: Lead):
    url = f"/api/clients/{lead.id}/lead-fields"
    created = lead.agency.post(url, json={"label": "Interest", "type": "select", "options": ["Hot"]})
    assert created.status_code == 201, created.text
    field = created.json()
    assert lead.agency.patch(f"{url}/{field['id']}", json={"label": "Level"}).json()["label"] == "Level"
    assert lead.admin.get(f"{lead.base}/lead-fields").json()[0]["label"] == "Level"
    assert lead.agency.delete(f"{url}/{field['id']}").status_code == 204
    assert lead.agency.get(url).json() == []
    assert lead.agency.get(f"/api/clients/{ZERO}/lead-fields").status_code == 404


# --- permission and function ---------------------------------------------------------------


def test_an_agent_role_edits_a_lead_but_not_the_fields(lead: Lead):
    field = _field(lead.admin, lead.base, "Note", "text")
    agent = lead.member("beto", "agent")
    assert agent.get(f"{lead.base}/lead-fields").status_code == 200
    assert agent.post(f"{lead.base}/lead-fields", json={"label": "X", "type": "text"}).json() == FORBIDDEN
    assert agent.patch(f"{lead.base}/lead-fields/{field['id']}", json={"label": "Y"}).json() == FORBIDDEN
    assert agent.delete(f"{lead.base}/lead-fields/{field['id']}").json() == FORBIDDEN
    assert agent.get(f"{lead.base}/lead-fields").json()[0]["label"] == "Note"

    filled = _patch(agent, lead.base, lead.cid, {"responsible_id": lead.members["beto"], "custom_values": {"note": "mine"}})
    assert filled.status_code == 200, filled.text
    assert filled.json()["responsible"]["name"] == "Beto" and filled.json()["custom_values"] == {"note": "mine"}


def test_the_permission_is_admin_only_and_belongs_to_the_inbox():
    from app.portal_permissions import FIELDS_MANAGE, PERMISSION_FEATURES, has_permission

    assert FIELDS_MANAGE == "fields.manage" and PERMISSION_FEATURES[FIELDS_MANAGE] == "inbox"
    assert has_permission("admin", FIELDS_MANAGE) and not has_permission("agent", FIELDS_MANAGE)


def test_the_session_lists_the_permission_for_admins_only(lead: Lead):
    agent = lead.member("beto", "agent")
    assert "fields.manage" in lead.admin.get(f"{lead.base}/me").json()["permissions"]
    assert "fields.manage" not in agent.get(f"{lead.base}/me").json()["permissions"]


def test_with_the_inbox_off_every_lead_route_answers_the_feature_refusal(lead: Lead):
    field = _field(lead.admin, lead.base, "Note", "text")
    assert lead.agency.patch(f"/api/clients/{lead.id}/portal", json={"portal_features": {"inbox": False}}).status_code == 200
    calls = [
        ("GET", f"/conversations/{lead.cid}/lead", None),
        ("PATCH", f"/conversations/{lead.cid}/lead", {}),
        ("GET", "/lead-fields", None),
        ("POST", "/lead-fields", {"label": "X", "type": "text"}),
        ("PATCH", f"/lead-fields/{field['id']}", {"label": "Y"}),
        ("DELETE", f"/lead-fields/{field['id']}", None),
    ]
    for method, path, body in calls:
        response = lead.admin.request(method, lead.base + path, json=body)
        assert response.status_code == 403 and response.json() == NO_FEATURE, (method, path)
    # The agency's panel ignores the portal's switches.
    assert lead.agency.get(f"/api/conversations/{lead.cid}/lead").status_code == 200


# --- ownership -----------------------------------------------------------------------------


def test_another_clients_conversation_field_and_member_are_not_found(lead: Lead, authenticated_client: TestClient):
    other = Lead(authenticated_client, "Other Co")
    other_field = _field(other.admin, other.base, "Secret", "text")

    assert lead.admin.get(f"{lead.base}/conversations/{other.cid}/lead").status_code == 404
    assert lead.admin.patch(f"{lead.base}/conversations/{other.cid}/lead", json={"custom_values": {}}).status_code == 404
    assert lead.admin.patch(f"{lead.base}/lead-fields/{other_field['id']}", json={"label": "Mine"}).status_code == 404
    assert lead.admin.delete(f"{lead.base}/lead-fields/{other_field['id']}").status_code == 404
    assert lead.admin.get(f"{lead.base}/lead-fields").json() == []
    assert lead.agency.patch(f"/api/clients/{lead.id}/lead-fields/{other_field['id']}", json={"label": "Mine"}).status_code == 404
    with TestingSession() as db:
        assert db.get(LeadField, uuid.UUID(other_field["id"])).label == "Secret"


def test_another_agency_sees_nothing(lead: Lead, authenticated_client: TestClient):
    field = _field(lead.admin, lead.base, "Note", "text")
    login_legacy_owner(authenticated_client)
    client = authenticated_client
    assert client.get(f"/api/conversations/{lead.cid}/lead").status_code == 404
    assert client.patch(f"/api/conversations/{lead.cid}/lead", json={"custom_values": {}}).status_code == 404
    assert client.get(f"/api/clients/{lead.id}/lead-fields").status_code == 404
    assert client.post(f"/api/clients/{lead.id}/lead-fields", json={"label": "X", "type": "text"}).status_code == 404
    assert client.delete(f"/api/clients/{lead.id}/lead-fields/{field['id']}").status_code == 404
    assert client.patch(f"/api/clients/{lead.id}/contacts/{ZERO}", json={"name": "X"}).status_code == 404


# --- contacts: company, and the agency's own edit routes ---------------------------------------


def _contact(lead: Lead, **fields) -> dict:
    created = lead.admin.post(f"{lead.base}/contacts", json={"name": "Rita", "phone": "573001112233", **fields})
    assert created.status_code == 201, created.text
    return created.json()


def test_the_portal_contact_takes_a_company(lead: Lead):
    contact = _contact(lead)
    assert contact["company"] is None
    changed = lead.admin.patch(f"{lead.base}/contacts/{contact['id']}", json={"company": "  Acme SA "})
    assert changed.status_code == 200 and changed.json()["company"] == "Acme SA"
    assert lead.admin.patch(f"{lead.base}/contacts/{contact['id']}", json={"name": "Rita G"}).json()["company"] == "Acme SA"
    assert lead.admin.patch(f"{lead.base}/contacts/{contact['id']}", json={"company": None}).json()["company"] is None
    # An empty company and a null e-mail both clear the value, on both doors.
    lead.admin.patch(f"{lead.base}/contacts/{contact['id']}", json={"company": "Acme", "email": "rita@example.com"})
    cleared = lead.admin.patch(f"{lead.base}/contacts/{contact['id']}", json={"company": "", "email": None})
    assert (cleared.json()["company"], cleared.json()["email"]) == (None, None)
    lead.admin.patch(f"{lead.base}/contacts/{contact['id']}", json={"company": "Acme", "email": "rita@example.com"})
    cleared = lead.agency.patch(f"/api/clients/{lead.id}/contacts/{contact['id']}", json={"company": "", "email": None})
    assert (cleared.json()["company"], cleared.json()["email"]) == (None, None)
    assert lead.admin.patch(f"{lead.base}/contacts/{contact['id']}", json={"company": "x" * 161}).status_code == 422


def test_the_agency_edits_a_contact_and_its_tags(lead: Lead):
    contact = _contact(lead)
    base = f"/api/clients/{lead.id}/contacts/{contact['id']}"
    changed = lead.agency.patch(base, json={"name": " Rita Gómez ", "email": "rita@example.com", "company": "Acme", "notes": "n", "phone": "+57 300 999 8877"})
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert (body["name"], body["email"], body["company"], body["notes"], body["phone"]) == ("Rita Gómez", "rita@example.com", "Acme", "n", "573009998877")
    assert body == lead.admin.get(f"{lead.base}/contacts/{contact['id']}").json()

    other = _contact(lead, name="Otro", phone="573005550000")
    clash = lead.agency.patch(base, json={"phone": other["phone"]})
    assert clash.status_code == 409
    assert lead.agency.patch(base, json={"phone": "12"}).status_code == 422

    tag = lead.agency.post(f"/api/clients/{lead.id}/contact-tags", json={"name": "VIP", "color": "#2f6df0"}).json()
    tagged = lead.agency.put(f"{base}/tags", json={"tag_ids": [tag["id"], ZERO]})
    assert tagged.status_code == 200, tagged.text
    assert [(t["id"], t["name"], t["color"]) for t in tagged.json()["tags"]] == [(tag["id"], "VIP", "#2f6df0")]
    assert lead.admin.get(f"{lead.base}/contacts/{contact['id']}").json()["tags"][0]["name"] == "VIP"
    assert lead.agency.put(f"{base}/tags", json={"tag_ids": []}).json()["tags"] == []


def test_the_agency_cannot_edit_a_contact_of_another_client(lead: Lead, authenticated_client: TestClient):
    other = Lead(authenticated_client, "Other Co")
    theirs = other.admin.post(f"{other.base}/contacts", json={"name": "Zed", "phone": "573007770000"}).json()
    assert lead.agency.patch(f"/api/clients/{lead.id}/contacts/{theirs['id']}", json={"name": "Mine"}).status_code == 404
    assert lead.agency.put(f"/api/clients/{lead.id}/contacts/{theirs['id']}/tags", json={"tag_ids": []}).status_code == 404
    assert other.admin.get(f"{other.base}/contacts/{theirs['id']}").json()["name"] == "Zed"


# --- the client's responsible person and currency ----------------------------------------------


def test_the_agency_sets_the_owner_and_the_currency(lead: Lead):
    assert lead.customer["owner_name"] is None and lead.customer["currency"] == "USD"
    changed = lead.agency.patch(f"/api/clients/{lead.id}", json={"owner_name": "  Dr. Ruiz ", "currency": "mxn"})
    assert changed.status_code == 200, changed.text
    assert (changed.json()["owner_name"], changed.json()["currency"]) == ("Dr. Ruiz", "MXN")
    assert lead.agency.get(f"/api/clients/{lead.id}").json()["currency"] == "MXN"
    assert lead.agency.patch(f"/api/clients/{lead.id}", json={"name": "Renamed"}).json()["owner_name"] == "Dr. Ruiz"
    assert lead.agency.patch(f"/api/clients/{lead.id}", json={"owner_name": None}).json()["owner_name"] is None
    assert lead.agency.patch(f"/api/clients/{lead.id}", json={"owner_name": "   "}).json()["owner_name"] is None
    for bad in ("XXX", "", None, "dollars"):
        assert lead.agency.patch(f"/api/clients/{lead.id}", json={"currency": bad}).status_code == 422, bad
    assert lead.agency.patch(f"/api/clients/{lead.id}", json={"owner_name": "x" * 121}).status_code == 422
    assert lead.agency.get(f"/api/clients/{lead.id}").json()["currency"] == "MXN"


def test_the_portal_details_edit_the_owner_and_the_currency(lead: Lead):
    assert lead.agency.patch(f"/api/clients/{lead.id}/portal", json={"portal_features": {"details": True}}).status_code == 200
    url = f"{lead.base}/client"
    body = lead.admin.get(url).json()
    assert body["owner_name"] is None and body["currency"] == "USD"

    changed = lead.admin.patch(url, json={"owner_name": " Dr. Ruiz ", "currency": "eur"})
    assert changed.status_code == 200, changed.text
    assert (changed.json()["owner_name"], changed.json()["currency"]) == ("Dr. Ruiz", "EUR")
    assert lead.agency.get(f"/api/clients/{lead.id}").json()["currency"] == "EUR"
    assert lead.admin.get(f"{lead.base}/pipeline/board").json()["currency"] == "EUR"

    assert lead.admin.patch(url, json={"currency": "XXX"}).status_code == 422
    assert lead.admin.patch(url, json={"currency": None}).status_code == 422
    assert lead.admin.patch(url, json={"owner_name": None}).json()["owner_name"] is None
    assert lead.admin.get(url).json()["currency"] == "EUR"
    # The portal still never changes what belongs to the agency.
    lead.admin.patch(url, json={"is_active": False, "portal_enabled": False, "portal_slug": "hijack"})
    stored = lead.agency.get(f"/api/clients/{lead.id}").json()
    assert stored["is_active"] is True and stored["portal_enabled"] is True and stored["portal_slug"] == lead.slug


def test_the_agency_board_carries_the_currency(lead: Lead):
    assert lead.agency.get(f"/api/clients/{lead.id}/pipeline/board").json()["currency"] == "USD"
    lead.agency.patch(f"/api/clients/{lead.id}", json={"currency": "PEN"})
    assert lead.agency.get(f"/api/clients/{lead.id}/pipeline/board").json()["currency"] == "PEN"
    assert lead.agency.get(f"/api/clients/{lead.id}/pipeline/board").json()["cards"] != []


def test_the_currency_list_matches_the_web_mirror():
    mirror = Path(__file__).resolve().parents[3] / "apps" / "web" / "lib" / "currencies.ts"
    if not mirror.exists():
        pytest.skip("the web app is not part of this checkout")
    listed = re.search(r"CURRENCIES\s*=\s*\[([^\]]*)\]", mirror.read_text(encoding="utf-8"))
    assert listed, "apps/web/lib/currencies.ts no longer declares CURRENCIES"
    assert tuple(re.findall(r'"([A-Z]{3})"', listed.group(1))) == CURRENCIES


# --- API credentials -------------------------------------------------------------------------


def _token(client: TestClient, preset: str) -> dict:
    integration = client.post("/api/integrations", json={"name": f"Key {preset}", "preset": preset}).json()
    token = client.post(f"/api/integrations/{integration['id']}/tokens", json={"expires_in_days": 30}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_scopes_guard_the_lead_card_routes(lead: Lead):
    reader = _token(lead.agency, "read_only")
    operator = _token(lead.agency, "operator")
    fields = f"/api/clients/{lead.id}/lead-fields"
    body = {"label": "Note", "type": "text"}

    assert lead.agency.get(f"/api/conversations/{lead.cid}/lead", headers=reader).status_code == 200  # inbox.read
    assert lead.agency.get(fields, headers=reader).status_code == 200
    refused = lead.agency.post(fields, headers=reader, json=body)
    assert refused.status_code == 403 and "lead_fields.manage" in refused.json()["detail"]
    created = lead.agency.post(fields, headers=operator, json=body)
    assert created.status_code == 201, created.text
    assert lead.agency.patch(f"/api/conversations/{lead.cid}/lead", headers=reader, json={}).status_code == 403
    assert lead.agency.patch(f"/api/conversations/{lead.cid}/lead", headers=operator, json={"custom_values": {"note": "x"}}).status_code == 200
    contact = _contact(lead)
    assert lead.agency.patch(f"/api/clients/{lead.id}/contacts/{contact['id']}", headers=reader, json={"company": "A"}).status_code == 403
    assert lead.agency.patch(f"/api/clients/{lead.id}/contacts/{contact['id']}", headers=operator, json={"company": "A"}).status_code == 200
