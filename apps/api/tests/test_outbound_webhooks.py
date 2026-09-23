"""Outbound webhooks: subscriptions, signed deliveries, retries and replay."""

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.models import ApiIntegration, Conversation, WebhookDelivery, WebhookSubscription, now_utc
from app.services import evolution as evolution_driver
from app.services import outbound_webhooks
from app.services import whatsapp_inbound as whatsapp_inbound_service
from app.services.ai import Completion
from conftest import TestingSession


def _integration(client: TestClient, scopes: list[str] | None = None) -> dict:
    created = client.post("/api/integrations", json={"name": "n8n", "scopes": scopes or ["inbox.read"]})
    assert created.status_code == 201, created.text
    return created.json()


def _subscribe(client: TestClient, integration_id: str, events: list | None = None) -> dict:
    response = client.post(f"/api/integrations/{integration_id}/webhooks", json={
        "url": "https://n8n.example/hook",
        "events": events if events is not None else ["message.received", "conversation.resolved", "deal.moved"],
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_crud_validates_url_and_events(authenticated_client: TestClient):
    client = authenticated_client
    integration = _integration(client)
    base = f"/api/integrations/{integration['id']}/webhooks"

    assert client.post(base, json={"url": "http://evil.test/hook", "events": ["message.received"]}).status_code == 400
    assert client.post(base, json={"url": "https://n8n.example/hook", "events": ["nope"]}).status_code == 400
    created = _subscribe(client, integration["id"])
    assert created["secret"].startswith("whsec_")

    rows = client.get(base).json()
    assert [row["id"] for row in rows] == [created["subscription_id"]]
    assert "secret" not in rows[0] and "encrypted_secret" not in rows[0]

    assert client.delete(f"{base}/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.delete(f"{base}/{created['subscription_id']}").status_code == 204
    assert client.get(base).json() == []


def _whatsapp_setup(client: TestClient, monkeypatch) -> tuple[dict, dict, dict]:
    customer = client.post("/api/clients", json={"name": "Shop", "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post("/api/agents", json={
        "client_id": customer["id"], "provider": "openrouter", "model": "gpt-4.1-mini",
        "name": "Support", "instructions": "", "personality": "", "is_active": True}).json()
    channel = client.put(f"/api/whatsapp/channels/{customer['id']}", json={"agent_id": agent["id"]}).json()
    from app.config import get_settings

    monkeypatch.setattr(evolution_driver, "send_text", AsyncMock(return_value="wa-out"))
    monkeypatch.setattr(evolution_driver, "mark_read", AsyncMock())
    monkeypatch.setattr(
        whatsapp_inbound_service, "run_completion", AsyncMock(return_value=Completion(text="Hi"))
    )
    return customer, agent, channel


def _inbound(client: TestClient, channel_id: str, message_id: str = "wa-in-1"):
    from app.config import get_settings

    return client.post(
        f"/api/internal/whatsapp/channels/{channel_id}/inbound",
        headers={"X-Bridge-Token": get_settings().whatsapp_bridge_token},
        json={"external_message_id": message_id, "remote_jid": "573001112233@s.whatsapp.net",
              "sender_name": "Sam", "text": "Hola"},
    )


def test_inbound_resolved_and_deal_emit_rows(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    customer, agent, channel = _whatsapp_setup(client, monkeypatch)
    integration = _integration(client)
    _subscribe(client, integration["id"], ["message.received"])
    other = _integration(client)
    _subscribe(client, other["id"], ["conversation.resolved"])

    assert _inbound(client, channel["id"]).status_code == 200
    with TestingSession() as db:
        rows = db.scalars(select(WebhookDelivery)).all()
        assert [(row.event, row.status) for row in rows] == [("message.received", "pending")]
        assert rows[0].payload["contact_name"] == "Sam"

    conversation = client.get("/api/conversations/inbox").json()[0]
    assert client.patch(f"/api/conversations/{conversation['id']}/status", json={"status": "resolved"}).status_code == 200
    with TestingSession() as db:
        events = sorted(row.event for row in db.scalars(select(WebhookDelivery)).all())
        assert events == ["conversation.resolved", "message.received"]


def test_revoked_integration_gets_no_rows(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    customer, agent, channel = _whatsapp_setup(client, monkeypatch)
    integration = _integration(client)
    _subscribe(client, integration["id"])
    with TestingSession() as db:
        row = db.scalars(select(ApiIntegration)).one()
        row.revoked_at = datetime.now(timezone.utc)
        db.commit()
    assert _inbound(client, channel["id"]).status_code == 200
    with TestingSession() as db:
        assert db.scalars(select(WebhookDelivery)).all() == []


def test_delivery_signs_retries_down_the_ladder_and_replays(authenticated_client: TestClient, monkeypatch):
    import httpx

    client = authenticated_client
    customer, agent, channel = _whatsapp_setup(client, monkeypatch)
    integration = _integration(client)
    created = _subscribe(client, integration["id"], ["message.received"])
    assert _inbound(client, channel["id"]).status_code == 200

    seen = {}

    class Response:
        status_code = 200

    class StreamClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, content=None, headers=None):
            seen["url"] = url
            seen["headers"] = headers
            seen["content"] = content
            return Response()

    monkeypatch.setattr(outbound_webhooks.httpx, "AsyncClient", StreamClient)
    with TestingSession() as db:
        assert asyncio.run(outbound_webhooks.process_due(db)) == 1
        row = db.scalars(select(WebhookDelivery)).one()
        assert row.status == "sent" and row.response_code == 200 and row.sent_at is not None
    assert seen["url"] == "https://n8n.example/hook"
    expected = hmac.new(created["secret"].encode(), seen["content"], hashlib.sha256).hexdigest()
    assert seen["headers"]["X-Signature"] == expected
    assert json.loads(seen["content"])["event"] == "message.received"

    # Failures climb 5, 15, 15 and 60 minutes, then rest as failed.
    async def boom(url, secret, payload):
        raise RuntimeError("n8n is down")

    monkeypatch.setattr(outbound_webhooks, "_post", boom)
    assert _inbound(client, channel["id"], "wa-in-2").status_code == 200
    with TestingSession() as db:
        for attempt, wait in [(1, 5), (2, 15), (3, 15), (4, 60)]:
            assert asyncio.run(outbound_webhooks.process_due(db)) == 1
            row = db.scalars(select(WebhookDelivery).where(WebhookDelivery.status == "pending")).one()
            assert row.attempts == attempt
            delta = (row.available_at - now_utc()).total_seconds()
            assert wait * 60 - 30 < delta <= wait * 60 + 30
            db.execute(update(WebhookDelivery).values(available_at=now_utc() - timedelta(seconds=1)))
            db.commit()
        assert asyncio.run(outbound_webhooks.process_due(db)) == 1
        assert db.scalars(select(WebhookDelivery).where(WebhookDelivery.status == "failed")).all().__len__() == 1

        failed = db.scalars(select(WebhookDelivery).where(WebhookDelivery.status == "failed")).one()
        replayed = client.post(
            f"/api/integrations/{integration['id']}/webhooks/{failed.subscription_id}/deliveries/{failed.id}/replay")
        assert replayed.status_code == 200, replayed.text
        assert replayed.json()["status"] == "pending" and replayed.json()["attempts"] == 0
