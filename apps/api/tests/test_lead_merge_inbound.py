"""What a channel does with a message that arrives on a thread merged into another lead.

A thread absorbed by a lead merge keeps receiving whatever its channel delivers.
The message must be found whatever the thread's status (a resolved linked thread
never opens a new lead) and its effects land on the lead: the primary reopens or
comes back from the archive, starts waiting, is touched and shows unread. The
lookup differs per channel (WhatsApp QR, WhatsApp API, Instagram/Messenger, the
web widget, the phone echo), so each path is held here.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.models import Conversation, Message, SocialChannel, WhatsAppChannel, now_utc
from app.routers import widget as widget_router
from app.services import ai as ai_service
from app.services import social_inbound
from conftest import TestingSession
from test_lead_card import Lead
from test_lead_merge import activity, conv, portal_items, portal_merge, state
from test_social_runtime import isolated_providers, resources
from test_whatsapp_cloud import _message, _receive, _setup_channel

CHAT = "573001112233@s.whatsapp.net"
# Imported so its autouse fixture stubs the messaging providers for every test here.
PROVIDER_STUBS = isolated_providers


def count(client_id) -> int:
    with TestingSession() as db:
        return db.scalar(select(func.count(Conversation.id)).where(Conversation.client_id == uuid.UUID(str(client_id)))) or 0


def texts(conversation_id: str) -> list[str]:
    with TestingSession() as db:
        rows = db.scalars(
            select(Message).where(Message.conversation_id == uuid.UUID(conversation_id), Message.kind == "message")
            .order_by(Message.created_at)
        ).all()
        return [row.content for row in rows]


def lead_for(client_id: str, agent_id: str, agency_id, name: str = "Primary", **fields) -> str:
    """A human-held web lead of the client, the primary the threads are merged into."""
    with TestingSession() as db:
        row = Conversation(agency_id=agency_id, client_id=uuid.UUID(client_id), agent_id=uuid.UUID(agent_id),
                           channel="widget", external_chat_id="widget:primary", contact_name=name, title=name,
                           mode="human", **fields)
        db.add(row)
        db.commit()
        return str(row.id)


def agency_merge(client, client_id: str, primary: str, secondary: str):
    return client.post(f"/api/clients/{client_id}/leads/merge",
                       json={"primary_conversation_id": primary, "secondary_conversation_id": secondary})


def resolve(client, conversation_id: str, archived: bool = False):
    assert client.patch(f"/api/conversations/{conversation_id}/status", json={"status": "resolved"}).status_code == 200
    if archived:
        with TestingSession() as db:
            for row in db.scalars(select(Conversation).where(
                    (Conversation.id == uuid.UUID(conversation_id)) | (Conversation.primary_conversation_id == uuid.UUID(conversation_id)))):
                row.archived_at = now_utc()
            db.commit()


def assert_lead_reopened(lead_id: str, thread_id: str, *, before):
    primary, thread = state(lead_id), state(thread_id)
    assert primary.status == "open" and thread.status == "open" and primary.resolved_at is None
    assert primary.archived_at is None and thread.archived_at is None
    assert primary.waiting_since is not None and primary.updated_at > before
    assert "reopened_by_contact" in [item["event"] for item in activity(lead_id)]


# --- WhatsApp QR ---------------------------------------------------------------------------------


@pytest.fixture
def qr(authenticated_client, monkeypatch):
    lead = Lead(authenticated_client)
    client = authenticated_client
    agent_id = lead.conversation["agent_id"]
    channel = client.put(f"/api/whatsapp/channels/{lead.id}", json={"agent_id": agent_id}).json()
    channel_id = uuid.UUID(channel["id"])
    with TestingSession() as db:
        row = db.get(WhatsAppChannel, channel_id)
        row.status = "connected"
        row.is_enabled = True
        db.commit()
    headers = {"X-Bridge-Token": get_settings().whatsapp_bridge_token}

    def inbound(mid: str, text: str = "hello"):
        response = client.post(f"/api/internal/whatsapp/channels/{channel_id}/inbound", headers=headers,
                               json={"external_message_id": mid, "remote_jid": CHAT, "text": text})
        assert response.status_code == 200, response.text
        return response.json()

    def phone(mid: str, text: str = "from the phone"):
        response = client.post(f"/api/internal/whatsapp/channels/{channel_id}/outgoing", headers=headers,
                               json={"external_message_id": mid, "remote_jid": CHAT, "text": text})
        assert response.status_code == 204, response.text

    lead.inbound, lead.phone, lead.agent_id = inbound, phone, agent_id
    return lead


@pytest.mark.parametrize("archived", [False, True])
def test_qr_message_on_a_resolved_linked_thread_reopens_the_lead_and_starts_no_new_one(qr, archived):
    thread = qr.inbound("m-1", "first")["conversation_id"]
    primary = conv(qr, "widget", "widget:p", name="Primary", mode="human")
    assert portal_merge(qr, primary, thread).status_code == 200
    assert qr.admin.patch(f"{qr.base}/conversations/{primary}/status", json={"status": "resolved"}).status_code == 200
    if archived:
        assert qr.admin.patch(f"{qr.base}/conversations/{primary}/archive", json={"archived": True}).status_code == 200
    assert state(thread).status == "resolved"
    before, existing = state(primary).updated_at, count(qr.id)
    answer = qr.inbound("m-2", "are you there?")
    assert answer["conversation_id"] == thread and answer["accepted"] is True
    # No new lead: the message lands on the thread it belongs to and reopens the lead.
    assert count(qr.id) == existing
    assert texts(thread)[-1] == "are you there?"
    assert_lead_reopened(primary, thread, before=before)
    # It shows unread on the lead (human-held, nobody assigned) and in the open list again.
    row = next(item for item in portal_items(qr) if item["id"] == primary)
    assert row["unread_count"] >= 1 and row["status"] == "open" and row["preview"] == "are you there?"
    assert thread not in [item["id"] for item in portal_items(qr)]
    assert row["channels"] == ["widget", "whatsapp"] and row["linked_count"] == 1


def test_qr_message_on_an_open_linked_thread_only_touches_the_lead(qr):
    thread = qr.inbound("m-1", "first")["conversation_id"]
    primary = conv(qr, "widget", "widget:p", name="Primary", mode="human")
    assert portal_merge(qr, primary, thread).status_code == 200
    with TestingSession() as db:
        db.get(Conversation, uuid.UUID(primary)).waiting_since = None
        db.commit()
    before = state(primary).updated_at
    qr.inbound("m-2", "second")
    assert state(primary).waiting_since is not None and state(primary).updated_at > before
    assert "reopened_by_contact" not in [item["event"] for item in activity(primary)]
    assert qr.admin.get(f"{qr.base}/conversations/{primary}").json()["reply_via_default"] == thread


def test_the_ai_keeps_answering_a_linked_thread_on_its_own_channel(qr):
    from app.services import whatsapp_inbound

    whatsapp_inbound.run_completion = AsyncMock(return_value=ai_service.Completion(text="We can help."))
    thread = qr.inbound("m-1", "first")["conversation_id"]
    primary = conv(qr, "widget", "widget:p", name="Primary", mode="ai")
    assert portal_merge(qr, primary, thread).status_code == 200
    answer = qr.inbound("m-2", "and this?")
    assert answer["conversation_id"] == thread and answer["reply"] == "We can help."
    assert state(thread).mode == "ai" and texts(thread)[-1] == "We can help."
    assert texts(primary) == []


def test_a_phone_reply_on_a_resolved_linked_thread_stays_on_it(qr):
    thread = qr.inbound("m-1", "first")["conversation_id"]
    primary = conv(qr, "widget", "widget:p", name="Primary", mode="human")
    assert portal_merge(qr, primary, thread).status_code == 200
    assert qr.admin.patch(f"{qr.base}/conversations/{primary}/status", json={"status": "resolved"}).status_code == 200
    existing = count(qr.id)
    qr.phone("phone-1", "answered from the phone")
    assert count(qr.id) == existing
    assert texts(thread)[-1] == "answered from the phone"
    with TestingSession() as db:
        assert db.get(Conversation, uuid.UUID(primary)).primary_conversation_id is None


def test_answering_on_a_linked_thread_answers_the_lead(qr, monkeypatch):
    monkeypatch.setattr("app.routers.portal.send_channel_message", AsyncMock(return_value="ext-qr"))
    thread = qr.inbound("m-1", "first")["conversation_id"]
    primary = conv(qr, "widget", "widget:p", name="Primary", mode="human", waiting_since=now_utc())
    assert portal_merge(qr, primary, thread).status_code == 200
    # Answering on the linked thread answers the lead: nobody waits any more.
    assert qr.admin.post(f"{qr.base}/conversations/{primary}/reply",
                         json={"content": "on it", "via_conversation_id": thread}).status_code == 200
    assert state(primary).waiting_since is None and state(primary).first_reply_at is not None


# --- WhatsApp API (Cloud) --------------------------------------------------------------------------


def test_cloud_message_on_a_resolved_linked_thread_reopens_the_lead(authenticated_client, monkeypatch):
    client = authenticated_client
    customer, agent, channel = _setup_channel(client)
    assert _receive(client, channel, _message(), monkeypatch).status_code == 200
    with TestingSession() as db:
        thread_row = db.scalars(select(Conversation)).one()
        thread, agency_id = str(thread_row.id), thread_row.agency_id
    primary = lead_for(customer["id"], agent["id"], agency_id)
    assert agency_merge(client, customer["id"], primary, thread).status_code == 200
    resolve(client, primary, archived=True)
    before, existing = state(primary).updated_at, count(customer["id"])
    response = _receive(client, channel, _message("wamid-in-2", "hello again"), monkeypatch, event_id="evt-2")
    assert response.status_code == 200, response.text
    assert count(customer["id"]) == existing
    assert texts(thread)[:3] == ["Hola", "Hola, Maria", "hello again"]
    assert_lead_reopened(primary, thread, before=before)


# --- Instagram and Messenger -------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["instagram", "messenger"])
def test_social_message_on_a_resolved_linked_thread_reopens_the_lead(authenticated_client, provider):
    from test_social_runtime import PERSON, inbound as social_event

    client = authenticated_client
    resource = resources(client, provider=provider, account=f"acct-{provider}")
    with TestingSession() as db:
        thread_row = social_event(db, resource, mid="in-1")
        thread, agency_id = str(thread_row.id), thread_row.agency_id
    primary = lead_for(str(resource.client_id), str(resource.agent_id), agency_id)
    assert agency_merge(client, str(resource.client_id), primary, thread).status_code == 200
    resolve(client, primary, archived=True)
    before, existing = state(primary).updated_at, count(resource.client_id)
    with TestingSession() as db:
        again = social_event(db, resource, mid="in-2", person=PERSON)
        assert str(again.id) == thread
    assert count(resource.client_id) == existing
    assert texts(thread)[-1] == "Hello"
    assert_lead_reopened(primary, thread, before=before)
    assert state(primary).waiting_since is not None


# --- the web widget ------------------------------------------------------------------------------------


def test_widget_visitor_writing_on_a_resolved_linked_thread_reopens_the_lead(authenticated_client, monkeypatch):
    lead = Lead(authenticated_client)
    client = authenticated_client
    public_id = client.put(
        f"/api/webchat/channels/{lead.id}",
        json={"agent_id": lead.conversation["agent_id"], "greeting": "Hi", "color": "#075985", "is_enabled": True},
    ).json()["public_id"]
    monkeypatch.setattr(widget_router, "run_completion", AsyncMock(return_value=ai_service.Completion(text="Sure!")))
    first = client.post(f"/api/widget/{public_id}/messages", json={"session_id": "s1", "content": "hello"})
    assert first.status_code == 200
    thread = first.json()["conversation_id"]
    primary = conv(lead, "whatsapp", "573009998888@s.whatsapp.net", name="Primary", mode="human")
    assert portal_merge(lead, primary, thread).status_code == 200
    assert lead.admin.patch(f"{lead.base}/conversations/{primary}/status", json={"status": "resolved"}).status_code == 200
    before, existing = state(primary).updated_at, count(lead.id)
    # The visitor's chat never reads as closed while its thread belongs to a lead.
    updates = client.get(f"/api/widget/{public_id}/updates?session_id=s1").json()
    assert updates["status"] == "open" and updates["conversation_id"] == thread
    history = client.get(f"/api/widget/{public_id}/history?session_id=s1").json()
    assert history["conversation_id"] == thread and history["status"] == "open"
    again = client.post(f"/api/widget/{public_id}/messages", json={"session_id": "s1", "content": "still there?"})
    assert again.status_code == 200 and again.json()["conversation_id"] == thread
    assert count(lead.id) == existing
    assert "still there?" in texts(thread)
    assert_lead_reopened(primary, thread, before=before)
    assert state(primary).waiting_since is not None


# --- the social echo, kept per thread ---------------------------------------------------------------------


def test_a_native_reply_echo_on_a_linked_social_thread_pauses_that_thread(authenticated_client):
    from test_social_runtime import event as social_payload, inbound as social_event

    client = authenticated_client
    resource = resources(client, account="acct-echo")
    with TestingSession() as db:
        thread_row = social_event(db, resource, mid="in-1")
        thread, agency_id = str(thread_row.id), thread_row.agency_id
    primary = lead_for(str(resource.client_id), str(resource.agent_id), agency_id)
    with TestingSession() as db:
        db.get(Conversation, uuid.UUID(primary)).mode = "ai"
        db.commit()
    assert agency_merge(client, str(resource.client_id), primary, thread).status_code == 200
    assert state(thread).mode == "ai"
    with TestingSession() as db:
        channel = db.get(SocialChannel, resource.channel_id)
        asyncio.run(social_inbound.process_event(db, channel, social_payload(resource, "echo-1", text="from the app", echo=True)))
    # Mode stays per thread: the person answered on this channel, so this thread waits for them.
    assert state(thread).mode == "human"
    assert texts(thread)[-1] == "from the app"


def test_an_escalation_on_a_linked_thread_hands_over_the_whole_lead(authenticated_client):
    from app.models import Agent, Team
    from app.services.escalation import EscalationRequest, apply_escalation

    lead = Lead(authenticated_client)
    primary = conv(lead, "widget", "widget:p", name="Primary", mode="ai")
    thread = conv(lead, "whatsapp", CHAT, name="Thread", mode="ai")
    assert portal_merge(lead, primary, thread).status_code == 200
    with TestingSession() as db:
        team = Team(client_id=uuid.UUID(lead.id), name="Support")
        db.add(team)
        db.flush()
        agent = db.get(Agent, uuid.UUID(lead.conversation["agent_id"]))
        agent.escalation_team_id = team.id
        db.commit()
        team_id = str(team.id)
        row = db.get(Conversation, uuid.UUID(thread))
        asyncio.run(apply_escalation(db, row, agent, EscalationRequest(reason="wants a person", trigger="human_request")))
    lead_row, thread_row = state(primary), state(thread)
    assert lead_row.mode == thread_row.mode == "human"
    assert str(lead_row.team_id) == str(thread_row.team_id) == team_id
    # The line about it is on the lead, not on the thread it happened on.
    assert "escalated" in [item["event"] for item in activity(primary)] and activity(thread) == []
