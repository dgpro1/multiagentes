"""A lead's name against the handle that stands in for it: a web chat that never
gave a name must not be labelled as if it had, and the handle the widget used to
write into the name field must never be read as a name again."""

from unittest.mock import AsyncMock
import uuid

from fastapi.testclient import TestClient

from app.models import Client, Conversation
from app.routers import widget as widget_router
from app.services import ai as ai_service
from app.services.lead_view import name_parts
from conftest import TestingSession


def _setup(client: TestClient, company: str):
    customer = client.post("/api/clients", json={"name": company, "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post(
        "/api/agents",
        json={"client_id": customer["id"], "provider": "openrouter", "model": "gpt-4.1-mini",
              "name": "Vera", "instructions": "", "personality": "", "is_active": True},
    ).json()
    return customer, agent


def _web_chat(client: TestClient, customer_id: str, agent_id: str, session_id: str, monkeypatch) -> dict:
    """A conversation opened the way a visitor opens one, through the widget."""
    client.put(f"/api/webchat/channels/{customer_id}",
               json={"agent_id": agent_id, "greeting": "Hi!", "color": "#075985", "is_enabled": True})
    public_id = client.get(f"/api/webchat/channels/{customer_id}").json()["public_id"]
    monkeypatch.setattr(widget_router, "run_completion", AsyncMock(return_value=ai_service.Completion(text="Hi!")))
    assert client.post(f"/api/widget/{public_id}/messages",
                       json={"session_id": session_id, "content": "hello"}).status_code == 200
    with TestingSession() as db:
        return db.query(Conversation).filter(Conversation.external_chat_id == f"widget:{session_id}").one()


def test_a_web_chat_that_never_gave_a_name_is_not_given_one(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    customer, agent = _setup(client, "Nameless Co")
    conversation = _web_chat(client, customer["id"], agent["id"], "s1", monkeypatch)

    assert conversation.contact_name is None, "the widget must not invent a name for an anonymous visitor"

    row = client.get("/api/conversations").json()[0]
    assert row["contact_name"] is None
    assert row["visitor_handle"] == "S1", "two nameless web chats still have to be told apart"


def test_a_handle_survives_where_a_name_would_have_been(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    customer, agent = _setup(client, "Handled Co")
    # What rows written before the fix carry. Kept so they read the same as the
    # ones written after it, without a migration over somebody's data.
    with TestingSession() as db:
        agency_id = db.get(Client, uuid.UUID(customer["id"])).agency_id
        conversation = Conversation(
            agency_id=agency_id, client_id=customer["id"], agent_id=agent["id"], number=1,
            channel="widget", external_chat_id="widget:9f8e7d6c-5b4a-3210-fedc-ba9876543210",
            contact_name="Visitor A1B2C3", title="Web chat",
        )
        db.add(conversation)
        db.commit()

    row = client.get("/api/conversations").json()[0]
    assert row["contact_name"] is None
    assert row["visitor_handle"] == "A1B2C3"


def test_a_name_somebody_gave_is_kept_and_gets_no_handle():
    assert name_parts(Conversation(channel="whatsapp", contact_name="Lucia")) == ("Lucia", None)


def test_the_rule_is_the_shape_the_widget_wrote_not_the_word():
    """Somebody whose name happens to read like a handle keeps it."""
    assert name_parts(Conversation(channel="whatsapp", contact_name="Visitor Smith")) == ("Visitor Smith", None)
    assert name_parts(Conversation(channel="widget", contact_name="Visitor Smith")) == ("Visitor Smith", None)


def test_a_lead_with_nothing_to_show_gets_neither():
    assert name_parts(Conversation(channel="whatsapp", external_chat_id="573@s.whatsapp.net")) == (None, None)
    assert name_parts(Conversation(channel="widget", contact_name="   ")) == (None, None)
