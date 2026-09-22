import hashlib
import hmac
import json
import uuid
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import TestingSession, login_legacy_owner

from app.models import Conversation, Message, WhatsAppCloudChannel
from app.routers import whatsapp_cloud as whatsapp_cloud_router
from app.services import ai as ai_service
from app.services import messaging_provider as provider_client
from app.services import whatsapp_cloud as whatsapp_cloud_service
from app.services import whatsapp_inbound as whatsapp_inbound_service


WEBHOOK_SECRET = "test-webhook-secret"


def _sign(raw: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _message(platform_id="wamid-in-1", text="Hola", conversation_id="conv-9", **extra):
    payload = {
        "id": f"mid-{platform_id}",
        "conversationId": conversation_id,
        "platform": "whatsapp",
        "platformMessageId": platform_id,
        "direction": "incoming",
        "text": text,
        "attachments": [],
        "sender": {"id": "573001112233", "name": "Maria", "phoneNumber": "+573001112233"},
    }
    payload.update(extra)
    return payload


def _event(account_id, message, event="message.received", event_id="evt-1"):
    return {
        "id": event_id,
        "event": event,
        "message": message,
        "account": {"accountId": account_id, "profileId": "prof-1"},
    }


def _post_signed(client: TestClient, payload: dict, secret: str = WEBHOOK_SECRET):
    raw = json.dumps(payload).encode()
    return client.post(
        "/api/public/messaging/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "X-Zernio-Signature": _sign(raw, secret)},
    )


def _setup_channel(client: TestClient, *, account_id: str = "acct-1") -> tuple[dict, dict, dict]:
    customer = client.post(
        "/api/clients",
        json={"name": "Bistro", "is_active": True},
    ).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post(
        "/api/agents",
        json={
            "client_id": customer["id"],
            "provider": "openrouter",
            "model": "gpt-4.1-mini",
            "name": "Host",
            "instructions": "",
            "personality": "",
            "is_active": True,
        },
    ).json()
    channel = client.put(
        f"/api/whatsapp-cloud/channels/{customer['id']}",
        json={"agent_id": agent["id"], "label": "Main"},
    ).json()
    with TestingSession() as db:
        row = db.get(WhatsAppCloudChannel, uuid.UUID(channel["id"]))
        row.external_account_id = account_id
        row.provider_profile_id = "prof-1"
        row.status = "connected"
        db.commit()
    return customer, agent, channel


def _channel_row(channel_id: str) -> WhatsAppCloudChannel:
    with TestingSession() as db:
        return db.get(WhatsAppCloudChannel, uuid.UUID(channel_id))


def test_configure_channel_shape(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent, channel = _setup_channel(client)
    assert channel["label"] == "Main"
    assert channel["has_access_token"] is False
    assert channel["has_app_secret"] is False
    assert channel["webhook_url"].endswith("/api/public/messaging/webhook")
    assert len(channel["webhook_verify_token"]) == 32
    assert channel["external_account_id"] == ""

    fetched = client.get(f"/api/whatsapp-cloud/channels/{customer['id']}").json()
    assert fetched["id"] == channel["id"]
    assert "access_token" not in fetched and "app_secret" not in fetched

    # Data belonging to an owner from an older installation stays isolated.
    login_legacy_owner(client)
    assert client.get(f"/api/whatsapp-cloud/channels/{customer['id']}").status_code == 404


def test_connect_verifies_a_linked_number(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    customer, _agent, channel = _setup_channel(client)

    fake_verify = AsyncMock(return_value={
        "display_phone_number": "+57 300 111 2233", "verified_name": "Bistro",
        "quality_rating": "GREEN", "messaging_limit": "TIER_1K", "username": "+573001112233"})
    monkeypatch.setattr(whatsapp_cloud_router, "verify_account", fake_verify)
    connected = client.post(f"/api/whatsapp-cloud/channels/{customer['id']}/connect").json()
    assert connected["status"] == "connected"
    assert connected["phone_number"] == "+57 300 111 2233"
    assert connected["display_name"] == "Bistro"
    assert connected["quality_rating"] == "GREEN"


def test_connect_returns_the_hosted_page_when_nothing_is_linked(authenticated_client: TestClient, monkeypatch):
    from app.services import messaging_profiles as profiles

    client = authenticated_client
    customer = client.post("/api/clients", json={"name": "Cafe", "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post("/api/agents", json={
        "client_id": customer["id"], "provider": "openrouter", "model": "gpt-4.1-mini",
        "name": "Host", "instructions": "", "personality": "", "is_active": True}).json()
    channel = client.put(f"/api/whatsapp-cloud/channels/{customer['id']}", json={"agent_id": agent["id"]}).json()

    monkeypatch.setattr(profiles, "ensure_channel_profile", AsyncMock(return_value="prof-9"))
    monkeypatch.setattr(provider_client, "connect_url",
                        AsyncMock(return_value={"authorization_url": "https://hosted.example/connect/9"}))
    connected = client.post(f"/api/whatsapp-cloud/channels/{channel['id']}/connect").json()
    assert connected["status"] == "disconnected"
    assert connected["connect_url"] == "https://hosted.example/connect/9"


def test_refresh_and_disconnect(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    _customer, _agent, channel = _setup_channel(client)
    monkeypatch.setattr(whatsapp_cloud_router, "verify_account",
                        AsyncMock(return_value={"display_phone_number": "+1", "verified_name": "B",
                                                "quality_rating": None, "messaging_limit": None, "username": "+1"}))
    assert client.post(f"/api/whatsapp-cloud/channels/{channel['id']}/refresh").json()["status"] == "connected"
    assert client.post(f"/api/whatsapp-cloud/channels/{channel['id']}/disconnect").json()["status"] == "disconnected"


def test_connect_callback_binds_the_number(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    customer = client.post("/api/clients", json={"name": "Deli", "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post("/api/agents", json={
        "client_id": customer["id"], "provider": "openrouter", "model": "gpt-4.1-mini",
        "name": "Host", "instructions": "", "personality": "", "is_active": True}).json()
    channel = client.put(f"/api/whatsapp-cloud/channels/{customer['id']}", json={"agent_id": agent["id"]}).json()
    with TestingSession() as db:
        row = db.get(WhatsAppCloudChannel, uuid.UUID(channel["id"]))
        row.provider_profile_id = "prof-9"
        db.commit()
    monkeypatch.setattr(whatsapp_cloud_service, "verify_account",
                        AsyncMock(return_value={"display_phone_number": "+99", "verified_name": "Deli",
                                                "quality_rating": "GREEN", "messaging_limit": "TIER_1K", "username": "+99"}))
    response = client.get("/api/public/messaging/connect/callback",
                          params={"connected": "whatsapp", "profileId": "prof-9", "accountId": "acct-9"},
                          follow_redirects=False)
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert f"/clients/{customer['id']}/channels/whatsapp-cloud" in location
    assert "messaging_status=ready" in location and f"line={channel['id']}" in location
    row = _channel_row(channel["id"])
    assert row.external_account_id == "acct-9" and row.status == "connected" and row.coexistence is False


def test_connect_callback_reports_a_refused_approval(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    customer = client.post("/api/clients", json={"name": "Deli", "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post("/api/agents", json={
        "client_id": customer["id"], "provider": "openrouter", "model": "gpt-4.1-mini",
        "name": "Host", "instructions": "", "personality": "", "is_active": True}).json()
    channel = client.put(f"/api/whatsapp-cloud/channels/{customer['id']}", json={"agent_id": agent["id"]}).json()
    with TestingSession() as db:
        row = db.get(WhatsAppCloudChannel, uuid.UUID(channel["id"]))
        row.provider_profile_id = "prof-9"
        db.commit()
    response = client.get("/api/public/messaging/connect/callback",
                          params={"error": "access_denied", "profileId": "prof-9"},
                          follow_redirects=False)
    assert response.status_code == 303, response.text
    assert "messaging_status=error" in response.headers["location"]
    assert _channel_row(channel["id"]).status == "disconnected"


def _receive(client, channel, message, monkeypatch, *, event_id="evt-1", reply="Hola, Maria", reply_id="wamid-out-1"):
    monkeypatch.setattr(whatsapp_inbound_service, "run_completion",
                        AsyncMock(return_value=ai_service.Completion(text=reply)))
    monkeypatch.setattr(whatsapp_cloud_service, "send_text", AsyncMock(return_value=reply_id))
    monkeypatch.setattr(provider_client, "mark_read", AsyncMock(return_value=None))
    monkeypatch.setattr(provider_client, "send_typing", AsyncMock(return_value=None))
    return _post_signed(client, _event("acct-1", message, event_id=event_id))


def test_inbound_text_creates_a_conversation_and_replies(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    _customer, _agent, channel = _setup_channel(client)
    response = _receive(client, channel, _message(), monkeypatch)
    assert response.status_code == 200, response.text
    with TestingSession() as db:
        conversation = db.scalars(select(Conversation)).one()
        assert conversation.channel == "whatsapp_cloud"
        assert conversation.external_chat_id == "573001112233"
        assert conversation.provider_conversation_id == "conv-9"
        texts = sorted(m.content for m in db.scalars(select(Message)).all())
        assert texts == ["Hola", "Hola, Maria"]
        outbound = db.scalars(select(Message).where(Message.role == "assistant")).one()
        assert outbound.external_message_id == "wamid-out-1"


def test_inbound_is_deduplicated_and_signed(authenticated_client: TestClient, monkeypatch):
    from sqlalchemy import select

    client = authenticated_client
    _customer, _agent, channel = _setup_channel(client)
    assert _receive(client, channel, _message(), monkeypatch).status_code == 200
    assert _receive(client, channel, _message(), monkeypatch, event_id="evt-2").status_code == 200
    with TestingSession() as db:
        assert db.scalars(select(Message).where(Message.role == "user")).all().__len__() == 1
    assert _post_signed(client, _event("acct-1", _message("wamid-other")), secret="wrong").status_code == 403
    unknown = _event("acct-unknown", _message("wamid-x"), event_id="evt-9")
    assert _post_signed(client, unknown).status_code == 200
    with TestingSession() as db:
        assert db.scalars(select(Message)).all().__len__() == 2


def test_receipts_are_monotonic_and_failures_stay_on_the_message(authenticated_client: TestClient, monkeypatch):
    from sqlalchemy import select

    client = authenticated_client
    _customer, _agent, channel = _setup_channel(client)
    assert _receive(client, channel, _message(), monkeypatch).status_code == 200
    for state in ("message.delivered", "message.read"):
        payload = _event("acct-1", {**_message(), "direction": "outgoing", "platformMessageId": "wamid-out-1",
                                    "sender": {"id": "business"}}, event=state, event_id=f"evt-{state}")
        assert _post_signed(client, payload).status_code == 200
    with TestingSession() as db:
        row = db.scalars(select(Message).where(Message.external_message_id == "wamid-out-1")).one()
        assert row.delivery_status == "read"
    # An earlier stage never downgrades a later one.
    payload = _event("acct-1", {**_message(), "direction": "outgoing", "platformMessageId": "wamid-out-1",
                                "sender": {"id": "business"}}, event="message.delivered", event_id="evt-late")
    assert _post_signed(client, payload).status_code == 200
    failed = _event("acct-1", {**_message(), "direction": "outgoing", "platformMessageId": "wamid-out-1",
                               "sender": {"id": "business"}, "error": "131026: no route"},
                    event="message.failed", event_id="evt-fail")
    assert _post_signed(client, failed).status_code == 200
    with TestingSession() as db:
        db.expire_all()
        row = db.scalars(select(Message).where(Message.external_message_id == "wamid-out-1")).one()
        assert row.delivery_status == "failed" and "131026" in (row.delivery_error or "")
        assert _channel_row(channel["id"]).last_error is None


def test_reactions_and_quotes(authenticated_client: TestClient, monkeypatch):
    from sqlalchemy import select

    client = authenticated_client
    _customer, _agent, channel = _setup_channel(client)
    assert _receive(client, channel, _message(), monkeypatch).status_code == 200
    reacted = _event("acct-1", {**_message(), "direction": "outgoing", "platformMessageId": "wamid-out-1",
                                "sender": {"id": "business"}}, event="reaction.received", event_id="evt-r1")
    reacted["reaction"] = {"platformMessageId": "wamid-out-1", "emoji": "👍", "action": "added"}
    assert _post_signed(client, reacted).status_code == 200
    with TestingSession() as db:
        row = db.scalars(select(Message).where(Message.external_message_id == "wamid-out-1")).one()
        assert row.incoming_reaction == "👍"
    reacted["reaction"] = {"platformMessageId": "wamid-out-1", "emoji": "", "action": "removed"}
    reacted["id"] = "evt-r2"
    assert _post_signed(client, reacted).status_code == 200
    with TestingSession() as db:
        db.expire_all()
        row = db.scalars(select(Message).where(Message.external_message_id == "wamid-out-1")).one()
        assert row.incoming_reaction is None

    quoted = _message("wamid-in-2", "Gracias", metadata={"quotedMessageId": "wamid-out-1"})
    assert _receive(client, channel, quoted, monkeypatch, event_id="evt-q", reply_id="wamid-out-2").status_code == 200
    with TestingSession() as db:
        visitor = db.scalars(select(Message).where(Message.external_message_id == "wamid-in-2")).one()
        target = db.scalars(select(Message).where(Message.external_message_id == "wamid-out-1")).one()
        assert visitor.quoted_message_id == target.id


def test_human_takeover_pauses_the_ai(authenticated_client: TestClient, monkeypatch):
    from sqlalchemy import select

    client = authenticated_client
    customer, _agent, channel = _setup_channel(client)
    with TestingSession() as db:
        conversation = Conversation(agency_id=db.get(WhatsAppCloudChannel, uuid.UUID(channel["id"])).agency_id,
            client_id=uuid.UUID(customer["id"]), agent_id=uuid.UUID(_agent["id"]), channel="whatsapp_cloud",
            whatsapp_cloud_channel_id=uuid.UUID(channel["id"]), external_chat_id="573001112233",
            provider_conversation_id="conv-9", mode="human", status="open", title="Case")
        db.add(conversation)
        db.commit()
    sent = AsyncMock(return_value="wamid-out-9")
    monkeypatch.setattr(whatsapp_cloud_service, "send_text", sent)
    monkeypatch.setattr(provider_client, "mark_read", AsyncMock(return_value=None))
    monkeypatch.setattr(provider_client, "send_typing", AsyncMock(return_value=None))
    assert _post_signed(client, _event("acct-1", _message(), event_id="evt-h")).status_code == 200
    sent.assert_not_called()
    with TestingSession() as db:
        assert db.scalars(select(Message).where(Message.role == "assistant")).all() == []


def test_ensure_webhook_registers_the_shared_endpoint(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    registered = AsyncMock(return_value={"url": "https://app.example/api/public/messaging/webhook",
                                         "events": ["message.received"], "isActive": True})
    monkeypatch.setattr(provider_client, "ensure_webhook", registered)
    response = client.post("/api/messaging/webhook/ensure")
    assert response.status_code == 200, response.text
    assert registered.call_args.args[1].endswith("/api/public/messaging/webhook")
    assert "message.received" in registered.call_args.args[3]
