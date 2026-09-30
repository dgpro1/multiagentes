import pytest
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from conftest import customer_conversation, login_legacy_owner

from app.database import SessionLocal
from app.models import Message, now_utc


def _portal(client: TestClient):
    customer = client.post(
        "/api/clients",
        json={"name": "Order Co", "is_active": True},
    ).json()
    client.post(f"/api/clients/{customer['id']}/portal-users", json={"name": "Ana", "email": "ana@order.co", "password": "secure-portal"})
    client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_enabled": True})
    agent = client.post(
        "/api/agents",
        json={"client_id": customer["id"], "name": "Host", "instructions": "", "personality": "", "model": "", "is_active": True},
    ).json()
    client.post(f"/api/portal/{customer['portal_slug']}/login", json={"email": "ana@order.co", "password": "secure-portal"})
    first = customer_conversation(client, agent["id"])["id"]
    second = customer_conversation(client, agent["id"])["id"]
    return customer["portal_slug"], first, second


def _visitor_wrote(conversation_id: str, minutes_ago: int) -> None:
    with SessionLocal() as db:
        db.add(
            Message(
                conversation_id=uuid.UUID(conversation_id),
                role="user",
                content="hola",
                sender_type="visitor",
                created_at=now_utc() - timedelta(minutes=minutes_ago),
            )
        )
        db.commit()


@pytest.mark.central_only("reads or writes through SessionLocal(), which has no client database by design")
def test_only_a_new_visitor_message_moves_a_conversation_up(authenticated_client: TestClient):
    client = authenticated_client
    slug, first, second = _portal(client)
    base = f"/api/portal/{slug}/conversations"

    def portal_order():
        return [row["id"] for row in client.get(f"{base}?status=open").json()]

    def agency_order():
        plain = [row["id"] for row in client.get("/api/conversations").json() if row["id"] in (first, second)]
        inbox = [row["id"] for row in client.get("/api/conversations/inbox").json() if row["id"] in (first, second)]
        assert plain == inbox
        return inbox

    _visitor_wrote(first, minutes_ago=10)
    _visitor_wrote(second, minutes_ago=5)
    assert portal_order() == [second, first]
    assert agency_order() == [second, first]

    # Opening the older one marks it read: it stays where it was.
    assert client.post(f"{base}/{first}/read").status_code == 204
    assert client.post(f"/api/conversations/{first}/read").status_code in (200, 204)
    assert portal_order() == [second, first]
    assert agency_order() == [second, first]

    # Working it (taking over, replying, resolving) does not move it either.
    client.patch(f"{base}/{first}/mode", json={"mode": "human"})
    client.post(f"{base}/{first}/reply", json={"content": "On it"})
    assert portal_order() == [second, first]
    assert agency_order() == [second, first]

    # The contact writing again is what brings it to the top.
    _visitor_wrote(first, minutes_ago=1)
    assert portal_order() == [first, second]
    assert agency_order() == [first, second]


def test_a_conversation_without_inbound_sorts_by_creation(authenticated_client: TestClient):
    client = authenticated_client
    slug, first, second = _portal(client)
    base = f"/api/portal/{slug}/conversations"
    # Nobody wrote yet: newest created first, and reading does not reorder.
    assert [row["id"] for row in client.get(f"{base}?status=open").json()] == [second, first]
    client.post(f"{base}/{first}/read")
    assert [row["id"] for row in client.get(f"{base}?status=open").json()] == [second, first]


def _pin(client: TestClient, conversation_id: str, pinned: bool):
    answered = client.patch(f"/api/conversations/{conversation_id}/pin", json={"pinned": pinned})
    assert answered.status_code == 200, answered.text
    return answered.json()


@pytest.mark.central_only("reads or writes through SessionLocal(), which has no client database by design")
def test_a_pinned_lead_goes_on_top_in_the_agency_and_the_portal_alike(authenticated_client: TestClient):
    """The pin belongs to the lead, not to a screen: whichever place presses it,
    both orders answer with it, and it beats a lead that arrived later."""
    client = authenticated_client
    slug, first, second = _portal(client)
    base = f"/api/portal/{slug}/conversations"

    def agency_order():
        return [row["id"] for row in client.get("/api/conversations/inbox").json() if row["id"] in (first, second)]

    def portal_order():
        return [row["id"] for row in client.get(f"{base}?status=open").json() if row["id"] in (first, second)]

    _visitor_wrote(first, minutes_ago=10)
    _visitor_wrote(second, minutes_ago=5)
    assert agency_order() == [second, first]
    assert portal_order() == [second, first]

    # From the agency.
    assert _pin(client, first, True)["pinned_at"] is not None
    assert agency_order() == [first, second]
    assert portal_order() == [first, second]
    # Every row that is not pinned stays in its own order, and says so.
    row = next(item for item in client.get("/api/conversations/inbox").json() if item["id"] == second)
    assert row["pinned_at"] is None

    # Unpinning puts the lead back where recency had it.
    assert _pin(client, first, False)["pinned_at"] is None
    assert agency_order() == [second, first]
    assert portal_order() == [second, first]


@pytest.mark.central_only("reads or writes through SessionLocal(), which has no client database by design")
def test_the_portal_pins_the_same_lead_the_agency_sees(authenticated_client: TestClient):
    client = authenticated_client
    slug, first, second = _portal(client)
    base = f"/api/portal/{slug}/conversations"
    _visitor_wrote(first, minutes_ago=10)
    _visitor_wrote(second, minutes_ago=5)

    pinned = client.patch(f"{base}/{first}/pin", json={"pinned": True})
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["pinned_at"] is not None
    assert [row["id"] for row in client.get(f"{base}?status=open").json() if row["id"] in (first, second)] == [first, second]
    assert [row["id"] for row in client.get("/api/conversations/inbox").json() if row["id"] in (first, second)] == [first, second]


def test_a_pin_answers_again_on_every_read_and_not_only_in_the_session_that_wrote_it(authenticated_client: TestClient):
    client = authenticated_client
    slug, first, second = _portal(client)
    base = f"/api/portal/{slug}/conversations"
    _pin(client, first, True)

    fresh = TestClient(client.app)
    fresh.cookies.update(client.cookies)
    listed = fresh.get("/api/conversations/inbox").json()
    row = next(item for item in listed if item["id"] == first)
    assert row["pinned_at"] is not None
    assert [item["id"] for item in listed if item["id"] in (first, second)] == [first, second]


def test_another_agency_neither_sees_nor_touches_the_pin(authenticated_client: TestClient):
    client = authenticated_client
    _slug, first, _second = _portal(client)
    _pin(client, first, True)

    # Public registration closes after the first agency, so the other agency is
    # seeded the way an installation that predates that rule looks.
    other = TestClient(client.app)
    other.post("/api/auth/logout")
    owner = login_legacy_owner(other)
    assert owner
    assert all(row["id"] != first for row in other.get("/api/conversations/inbox").json())
    assert other.patch(f"/api/conversations/{first}/pin", json={"pinned": True}).status_code == 404
    # The lead is still there, and still pinned, where it belongs.
    assert next(row for row in client.get("/api/conversations/inbox").json() if row["id"] == first)["pinned_at"] is not None



def test_the_portal_refuses_a_lead_of_another_client(authenticated_client: TestClient):
    """A portal session is tied to its own client, so a lead of somebody else is
    a lead that does not exist there."""
    client = authenticated_client
    slug, _first, _second = _portal(client)
    stranger = client.post("/api/clients", json={"name": "Otro cliente", "is_active": True}).json()
    agent = client.post(
        "/api/agents",
        json={"client_id": stranger["id"], "name": "Otro", "instructions": "", "personality": "", "model": "", "is_active": True},
    ).json()
    other_lead = customer_conversation(client, agent["id"])
    assert client.patch(f"/api/portal/{slug}/conversations/{other_lead['id']}/pin", json={"pinned": True}).status_code == 404


def test_a_pinned_lead_still_answers_no_reply_instead_of_hiding_behind_it(authenticated_client: TestClient):
    """The pin and "no reply" are two different questions, and one does not
    answer for the other."""
    client = authenticated_client
    slug, first, second = _portal(client)
    base = f"/api/portal/{slug}/conversations"
    _visitor_wrote(first, minutes_ago=10)
    _visitor_wrote(second, minutes_ago=5)

    def pending():
        return [row["id"] for row in client.get("/api/conversations/inbox?pending=1").json() if row["id"] in (first, second)]

    assert pending() == [second, first]
    _pin(client, first, True)
    assert pending() == [first, second], "pinned and waiting for an answer at the same time"

    # Answering takes it out of the filter; the pin does not put it back.
    client.patch(f"{base}/{first}/status", json={"status": "resolved"})
    assert pending() == [second]
    assert [row["id"] for row in client.get("/api/conversations/inbox").json() if row["id"] in (first, second)] == [first, second]


def test_opening_a_lead_does_not_answer_it(authenticated_client: TestClient):
    """`unread` is about who has looked and the inbox clears it on open. The dot
    is about the conversation, so opening a lead is not the same as resolving it."""
    client = authenticated_client
    _slug, first, _second = _portal(client)
    _visitor_wrote(first, minutes_ago=10)

    def row():
        return next(item for item in client.get("/api/conversations/inbox").json() if item["id"] == first)

    assert row()["awaiting_reply"] is True
    assert client.post(f"/api/conversations/{first}/read").status_code in (200, 204)
    after = row()
    assert after["unread"] is False and after["unread_count"] == 0
    assert after["awaiting_reply"] is True, "reading a lead is not answering it"
    assert client.get("/api/conversations/inbox?pending=1").json()[0]["id"] == first


def test_a_row_carries_the_client_slug_and_the_lead_number_to_build_its_own_address(authenticated_client: TestClient):
    """The address of a lead is the client's slug and the lead's number, never
    the client's id, so the agency-wide list has to hand both over."""
    client = authenticated_client
    customer = client.post("/api/clients", json={"name": "Address Co", "is_active": True}).json()
    agent = client.post(
        "/api/agents",
        json={"client_id": customer["id"], "name": "Host", "instructions": "", "personality": "", "model": "", "is_active": True},
    ).json()
    lead = customer_conversation(client, agent["id"])
    row = next(item for item in client.get("/api/conversations/inbox").json() if item["id"] == lead["id"])
    assert row["client_slug"] == customer["portal_slug"]
    assert row["number"] == lead["number"]
