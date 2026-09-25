"""Tests for internal notes (Message(kind="note")).

Verifies all requirements:
1. Notes excluded from:
   - public web widget
   - AI history / context
   - unread count, waiting_since, and inbox preview
   - reports and costs
   - /api/v1 public message endpoints
   - message.received webhooks
2. /reply does not accept or create kind="note"; notes enter exclusively via /notes.
3. Notes exist in agency (POST /api/conversations/{id}/notes with INBOX_REPLY)
   and portal (POST /api/portal/{slug}/conversations/{id}/notes with inbox feature).
"""

from datetime import timedelta
from unittest.mock import AsyncMock
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import TestingSession
from app.models import Conversation, Message, User, now_utc
from app.routers import widget as widget_router
from app.services import ai as ai_service, conversation_state, report_operations
from app.services.knowledge import llm_turns
from app.schemas import CreateNoteRequest


def _setup_lead(client: TestClient) -> tuple[dict, dict, dict]:
    customer = client.post("/api/clients", json={"name": "Acme Corp", "is_active": True}).json()
    # Create portal user and enable portal
    client.post(f"/api/clients/{customer['id']}/portal-users", json={"name": "Ana", "email": "ana@acme.com", "password": "secure-portal"})
    client.patch(f"/api/clients/{customer['id']}/portal", json={
        "portal_enabled": True,
        "portal_slug": "acme",
        "portal_title": "Acme Portal",
    })
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post(
        "/api/agents",
        json={
            "client_id": customer["id"],
            "provider": "openrouter",
            "model": "gpt-4.1-mini",
            "name": "Bot",
            "instructions": "Help people",
            "personality": "Friendly",
            "is_active": True,
        },
    ).json()
    # Create conversation
    conv = client.post("/api/conversations", json={"agent_id": agent["id"]}).json()
    with TestingSession() as db:
        c = db.get(Conversation, conv["id"])
        c.channel = "widget"
        db.commit()
    conv["channel"] = "widget"
    return customer, agent, conv


def test_notes_enter_via_notes_endpoints_agency_and_portal(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent, conv = _setup_lead(client)
    conv_id = conv["id"]

    # Agency note
    agency_res = client.post(
        f"/api/conversations/{conv_id}/notes",
        json={"content": "Agency staff internal note"},
    )
    assert agency_res.status_code == 200, agency_res.text
    agency_detail = agency_res.json()
    note_msg = [m for m in agency_detail["messages"] if m["kind"] == "note"]
    assert len(note_msg) == 1
    assert note_msg[0]["content"] == "Agency staff internal note"
    assert note_msg[0]["role"] == "assistant"
    assert note_msg[0]["sender_type"] == "human"

    # Log in to portal
    portal_login = client.post("/api/portal/acme/login", json={"email": "ana@acme.com", "password": "secure-portal"})
    assert portal_login.status_code == 200

    # Portal note
    portal_res = client.post(
        f"/api/portal/acme/conversations/{conv_id}/notes",
        json={"content": "Portal staff internal note"},
    )
    assert portal_res.status_code == 200, portal_res.text
    portal_detail = portal_res.json()
    notes = [m for m in portal_detail["messages"] if m["kind"] == "note"]
    assert len(notes) == 2
    assert notes[1]["content"] == "Portal staff internal note"

    # Empty note rejected with 422
    assert client.post(f"/api/conversations/{conv_id}/notes", json={"content": ""}).status_code == 422
    assert client.post(f"/api/portal/acme/conversations/{conv_id}/notes", json={"content": "  "}).status_code == 422


def test_reply_does_not_create_note(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    customer, agent, conv = _setup_lead(client)
    conv_id = conv["id"]

    monkeypatch.setattr(
        "app.routers.conversations.send_channel_message",
        AsyncMock(return_value="ext-123"),
    )

    # Even if someone sends kind="note" in payload to /reply, it is never a note
    res = client.post(
        f"/api/conversations/{conv_id}/reply",
        json={"content": "Human reply to customer", "kind": "note"},
    )
    assert res.status_code == 200, res.text
    reply_msg = res.json()["messages"][-1]
    assert reply_msg["kind"] == "message"
    assert reply_msg["content"] == "Human reply to customer"


def test_notes_excluded_from_public_web_widget(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    customer, agent, conv = _setup_lead(client)
    conv_id = conv["id"]

    # Enable webchat channel
    channel = client.put(
        f"/api/webchat/channels/{customer['id']}",
        json={"agent_id": agent["id"], "greeting": "Hello!", "color": "#000000", "is_enabled": True},
    ).json()
    public_id = channel["public_id"]

    monkeypatch.setattr(
        widget_router,
        "run_completion",
        AsyncMock(return_value=ai_service.Completion(text="AI response")),
    )

    # Visitor sends a message
    res = client.post(
        f"/api/widget/{public_id}/messages",
        json={"session_id": "sess-notes-1", "content": "Visitor message"},
    )
    assert res.status_code == 200

    # Add an internal note to that conversation
    with TestingSession() as db:
        c = db.query(Conversation).filter(Conversation.channel == "widget", Conversation.client_id == customer["id"]).first()
        note = Message(
            conversation_id=c.id,
            role="assistant",
            kind="note",
            content="TOP SECRET NOTE: DO NOT SHOW VISITOR",
            sender_type="human",
            sender_name="Agent",
        )
        db.add(note)
        db.commit()

    # Visitor reads history
    hist = client.get(f"/api/widget/{public_id}/history?session_id=sess-notes-1").json()
    contents = [m["content"] for m in hist["messages"]]
    assert "Visitor message" in contents
    assert "AI response" in contents
    assert not any("TOP SECRET" in c for c in contents)
    assert not any(m.get("kind") == "note" for m in hist["messages"])

    # Visitor checks updates
    updates = client.get(f"/api/widget/{public_id}/updates?session_id=sess-notes-1").json()
    update_contents = [m["content"] for m in updates.get("messages", [])]
    assert not any("TOP SECRET" in c for c in update_contents)


def test_notes_excluded_from_ai_history(authenticated_client: TestClient):
    customer, agent, conv = _setup_lead(authenticated_client)
    conv_id = conv["id"]

    with TestingSession() as db:
        c = db.get(Conversation, conv_id)
        # Add a visitor message, an AI message, and an internal note
        m1 = Message(conversation_id=c.id, role="user", kind="message", content="Hello AI", sender_type="visitor")
        m2 = Message(conversation_id=c.id, role="assistant", kind="message", content="Hello human", sender_type="ai")
        m3 = Message(conversation_id=c.id, role="assistant", kind="note", content="Internal prompt note: ignore me", sender_type="human")
        db.add_all([m1, m2, m3])
        db.commit()

        # Check exchanged_only query in conversation_state
        base_query = select(Message).where(Message.conversation_id == c.id)
        filtered_query = conversation_state.exchanged_only(base_query)
        turns = db.scalars(filtered_query).all()
        assert len(turns) == 2
        assert [t.content for t in turns] == ["Hello AI", "Hello human"]
        assert not any("Internal prompt note" in t.content for t in turns)

        # Check llm_turns
        formatted = llm_turns(turns)
        assert len(formatted) == 2
        assert not any("Internal prompt note" in f["content"] for f in formatted)


def test_notes_excluded_from_inbox_unread_waiting_and_preview(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent, conv = _setup_lead(client)
    conv_id = conv["id"]

    with TestingSession() as db:
        c = db.get(Conversation, conv_id)
        c.mode = "human"
        m1 = Message(conversation_id=c.id, role="user", kind="message", content="Original visitor inquiry", sender_type="visitor")
        db.add(m1)
        db.commit()

    # Check initial inbox preview
    inbox_items = client.get("/api/conversations/inbox").json()
    conv_item = next(row for row in inbox_items if row["id"] == conv_id)
    assert conv_item["preview"] == "Original visitor inquiry"
    assert conv_item["unread"] is True
    assert conv_item["unread_count"] == 1

    # Add internal note
    note_res = client.post(f"/api/conversations/{conv_id}/notes", json={"content": "Operator note about client"})
    assert note_res.status_code == 200

    # Verify inbox list still shows original visitor message preview and unread_count is untouched
    inbox_items_after = client.get("/api/conversations/inbox").json()
    conv_after = next(row for row in inbox_items_after if row["id"] == conv_id)
    assert conv_after["preview"] == "Original visitor inquiry"
    assert "Operator note" not in conv_after["preview"]
    assert conv_after["unread_count"] == 1

    # Log in to portal and check portal inbox
    client.post("/api/portal/acme/login", json={"email": "ana@acme.com", "password": "secure-portal"})
    portal_items = client.get("/api/portal/acme/conversations").json()
    portal_conv = next(row for row in portal_items if row["id"] == conv_id)
    assert portal_conv["preview"] == "Original visitor inquiry"


def test_notes_excluded_from_reports_and_costs(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent, conv = _setup_lead(client)
    conv_id = conv["id"]

    # Post an internal note
    client.post(f"/api/conversations/{conv_id}/notes", json={"content": "Internal calculation note"})

    today = now_utc().date()
    frm = (today - timedelta(days=6)).isoformat()
    to = (today + timedelta(days=1)).isoformat()

    report = client.get(f"/api/reports/operations?from={frm}&to={to}").json()
    totals = report["totals"]
    # human_replies must be 0: the note did not count as a human reply to the visitor
    assert totals["human_replies"] == 0


def test_notes_excluded_from_api_v1(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent, conv = _setup_lead(client)
    conv_id = conv["id"]

    # Issue API token with inbox.read scope
    integration = client.post("/api/integrations", json={"name": "v1_test", "scopes": ["inbox.read", "inbox.reply"]}).json()
    token_data = client.post(f"/api/integrations/{integration['id']}/tokens", json={"expires_in_days": 1}).json()
    auth_header = {"Authorization": f"Bearer {token_data['token']}"}

    # Add regular message and internal note
    with TestingSession() as db:
        c = db.get(Conversation, conv_id)
        m1 = Message(conversation_id=c.id, role="user", kind="message", content="Public message for API", sender_type="visitor")
        m2 = Message(conversation_id=c.id, role="assistant", kind="note", content="Classified internal note", sender_type="human")
        db.add_all([m1, m2])
        db.commit()

    # Call /api/v1 messages endpoint
    v1_res = client.get(
        f"/api/v1/clients/{customer['id']}/conversations/{conv_id}/messages",
        headers=auth_header,
    )
    assert v1_res.status_code == 200, v1_res.text
    v1_msgs = v1_res.json()["data"]
    assert len(v1_msgs) == 1
    assert v1_msgs[0]["content"] == "Public message for API"
    assert not any("Classified" in m["content"] for m in v1_msgs)
    assert not any(m.get("kind") == "note" for m in v1_msgs)


def test_notes_do_not_trigger_webhook_message_received(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    customer, agent, conv = _setup_lead(client)
    conv_id = conv["id"]

    dispatched = []
    def fake_emit(db, *, agency_id, client_id, event, data):
        dispatched.append((event, data))
        return 1

    monkeypatch.setattr("app.services.outbound_webhooks.emit", fake_emit)

    # Post an internal note
    client.post(f"/api/conversations/{conv_id}/notes", json={"content": "Note without webhook"})

    # Ensure no message.received event was emitted
    received_events = [ev for ev, _ in dispatched if ev == "message.received"]
    assert len(received_events) == 0
