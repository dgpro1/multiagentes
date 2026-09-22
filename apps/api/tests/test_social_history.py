import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

from sqlalchemy import select

from app.models import Agent, SocialChannel, SocialHistoryImport, SocialWebhookEvent, now_utc
from app.services import social_history as history
from conftest import TestingSession, login_legacy_owner


def setup(client):
    customer = client.post("/api/clients", json={"name": "Shop", "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post("/api/agents", json={"client_id": customer["id"], "provider": "openrouter", "model": "gpt-4.1-mini", "name": "Support", "instructions": "", "personality": "", "is_active": True}).json()
    with TestingSession() as db:
        agent_row = db.get(Agent, agent["id"])
        channel = SocialChannel(agency_id=agent_row.agency_id, client_id=agent_row.client_id, agent_id=agent_row.id,
            provider="instagram", external_account_id="acct-1", provider_profile_id="prof-1",
            display_name="Shop", status="connected", is_enabled=True, connection_source="oauth",
            last_connected_at=now_utc().replace(microsecond=0))
        db.add(channel)
        db.commit()
    return f"/api/social/instagram/channels/{customer['id']}/import-history"


def thread(thread_id="thread-1", participant="person-1"):
    return {"id": thread_id, "participantId": participant, "participantName": "Visitor"}


def item(mid, *, direction="incoming", text="Hello", created=None):
    return {"id": mid, "conversationId": "thread-1", "platform": "instagram", "message": text,
            "direction": direction, "createdAt": (created or now_utc()).isoformat()}


def test_history_is_authenticated_and_repeated_requests_reuse_active_job(authenticated_client):
    client = authenticated_client
    endpoint = setup(client)
    first = client.post(endpoint)
    assert first.status_code == 202, first.text
    assert first.json()["max_conversations"] == 20
    assert first.json()["limited"] is True
    assert client.post(endpoint).json()["id"] == first.json()["id"]
    login_legacy_owner(client)
    assert client.get(endpoint).status_code == 404
    assert client.post(endpoint).status_code == 404


def test_history_uses_cutoff_ascending_order_and_opaque_ids(authenticated_client, monkeypatch):
    from app.services import messaging_provider as provider_client

    client = authenticated_client
    endpoint = setup(client)
    client.post(endpoint)
    old = now_utc() - timedelta(days=3)
    recent = now_utc() - timedelta(days=1)
    future = now_utc() + timedelta(minutes=1)
    monkeypatch.setattr(provider_client, "list_conversations", AsyncMock(return_value=([thread()], "cursor-2")))
    monkeypatch.setattr(provider_client, "list_messages", AsyncMock(return_value=([
        item("recent", text="Reply", created=recent),
        item("old", text="Earlier", created=old),
        item("future", text="Live", created=future),
    ], {})))
    with TestingSession() as db:
        assert asyncio.run(history.process_history_jobs(db)) == 1
        job = db.scalar(select(SocialHistoryImport))
        assert job.status == "pending" and job.cursor == "cursor-2"
        assert job.messages_count == 2 and job.conversations_count == 1
        events = db.scalars(select(SocialWebhookEvent).order_by(SocialWebhookEvent.created_at)).all()
        assert [event.payload["message"]["text"] for event in events] == ["Earlier", "Reply"]
        assert all(event.payload["_historical"] for event in events)


def test_history_limit_checkpoint_and_next_batch_continuation(authenticated_client, monkeypatch):
    from app.services import messaging_provider as provider_client

    client = authenticated_client
    endpoint = setup(client)
    first = client.post(endpoint).json()
    monkeypatch.setattr(provider_client, "list_conversations", AsyncMock(return_value=([thread()], "next-page")))
    monkeypatch.setattr(provider_client, "list_messages", AsyncMock(return_value=([], {})))
    with TestingSession() as db:
        job = db.scalar(select(SocialHistoryImport))
        job.conversations_count = 19
        db.commit()
        asyncio.run(history.process_history_jobs(db))
        assert job.status == "completed"
        assert job.conversations_count == 20
        cutoff = job.cutoff_at
    assert client.get(endpoint).json()["has_more"] is True
    second = client.post(endpoint)
    assert second.status_code == 202
    assert second.json()["id"] != first["id"]
    with TestingSession() as db:
        latest = db.scalar(select(SocialHistoryImport).order_by(SocialHistoryImport.created_at.desc()))
        assert latest.cursor == "next-page" and latest.cutoff_at == cutoff


def test_history_bounds_messages_and_does_not_hide_network_failures(authenticated_client, monkeypatch):
    from fastapi import HTTPException

    from app.services import messaging_provider as provider_client

    client = authenticated_client
    endpoint = setup(client)
    client.post(endpoint)
    calls = []

    async def fail(account_id, conversation_id, **kwargs):
        calls.append(conversation_id)
        raise HTTPException(502, "The messaging provider could not be reached")

    monkeypatch.setattr(provider_client, "list_conversations",
                        AsyncMock(return_value=([thread(f"thread-{index}") for index in range(50)], None)))
    monkeypatch.setattr(provider_client, "list_messages", fail)
    with TestingSession() as db:
        asyncio.run(history.process_history_jobs(db))
        job = db.scalar(select(SocialHistoryImport))
        assert job.status == "failed" and job.conversations_count == 0
        assert job.messages_count == 0
        assert not db.scalar(select(SocialWebhookEvent))
    assert len(calls) == 1


def test_history_rejects_foreign_and_group_messages():
    class Channel:
        external_account_id = "acct-1"

    cutoff = now_utc()
    message = {"id": "message", "createdAt": (cutoff - timedelta(days=1)).isoformat(),
               "direction": "incoming", "message": "Foreign"}
    assert history.normalize_message(Channel(), thread(participant="acct-1"), message, cutoff) is None
    assert history.normalize_message(Channel(), thread(), {**message, "id": ""}, cutoff) is None
    assert history.normalize_message(Channel(), thread(), {**message, "direction": "outgoing"}, cutoff) is not None
