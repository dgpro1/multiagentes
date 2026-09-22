"""The Evolution API QR driver: configuration, instance lifecycle, webhook
events, and the outbound seams."""

import asyncio
import base64
import json
import uuid

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.models import WhatsAppChannel
from app.services import ai as ai_service
from app.services import evolution as evolution_driver
from app.services import whatsapp_inbound as whatsapp_inbound_service


def _env(monkeypatch, **overrides):
    values = {
        "EVOLUTION_API_URL": "http://127.0.0.1:9",
        "EVOLUTION_API_KEY": "test-evolution-key",
        "EVOLUTION_WEBHOOK_SECRET": "test-evolution-webhook-secret",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    from app.config import get_settings

    get_settings.cache_clear()


def _setup_line(client):
    customer = client.post("/api/clients", json={"name": "Cafe Evolution", "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post("/api/agents", json={
        "client_id": customer["id"], "provider": "openrouter", "model": "openai/gpt-5.6-luna",
        "name": "Evo", "instructions": "", "personality": "", "is_active": True}).json()
    line = client.post(f"/api/whatsapp/clients/{customer['id']}/channels", json={"agent_id": agent["id"]}).json()
    return customer, line


def _stub_http(monkeypatch, *, state="close"):
    """Replace the Evolution HTTP layer with a fake that records calls."""
    calls = []

    async def fake_request(method, path, *, json=None, timeout=30):
        calls.append((method, path))
        if path.startswith("/instance/fetchInstances"):
            if not fake_request.instance_exists:
                return {}
            return {"instance": {"instanceName": "openlivery-x", "connectionStatus": {"state": state},
                                 "ownerJid": "573001112233@s.whatsapp.net", "profileName": "Cafe Bot"}}
        if path == "/instance/create":
            fake_request.instance_exists = True
            return {"instance": {"instanceName": "openlivery-x"}}
        if path.startswith("/instance/connect/"):
            return {"qrcode": {"base64": "data:image/png;base64,QR1", "count": 1}}
        if path.startswith("/instance/connectionState/"):
            return {"instance": {"state": state}}
        if path.startswith("/instance/logout") or path.startswith("/instance/delete"):
            fake_request.instance_exists = False
            return {}
        if path.startswith("/message/sendText"):
            return {"key": {"id": "wamid-evolution-out-1"}}
        if path.startswith("/message/sendMedia"):
            return {"key": {"id": "wamid-evolution-media-1"}}
        if path.startswith("/message/sendLocation"):
            return {"key": {"id": "wamid-evolution-location-1"}}
        if path.startswith("/settings/set"):
            return {}
        if path.startswith("/group/findGroupInfos"):
            return {"subject": "Family group"}
        if path.startswith("/chat/markMessageAsRead"):
            return {"success": True}
        if path.startswith("/chat/sendPresence"):
            return {}
        if path.startswith("/message/sendReaction"):
            return {}
        if path.startswith("/webhook/set"):
            return {}
        raise AssertionError(f"unexpected call {method} {path}")

    fake_request.instance_exists = False
    fake_request.calls = calls
    monkeypatch.setattr(evolution_driver, "request", fake_request)
    return calls


def _webhook(client, payload, *, secret="test-evolution-webhook-secret"):
    return client.post(
        "/api/public/whatsapp/evolution/webhook",
        content=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
    )


def _event(instance, data, event):
    return {"event": event, "instance": instance, "data": data}


@pytest.fixture()
def evolution_client(authenticated_client, monkeypatch):
    """Authenticated client with the Evolution driver configured (no HTTP)."""
    _env(monkeypatch)
    _stub_http(monkeypatch)
    return authenticated_client


def test_driver_selection(monkeypatch):
    _env(monkeypatch)  # url+key set
    assert evolution_driver.enabled() is True
    _env(monkeypatch, EVOLUTION_API_KEY="")
    assert evolution_driver.enabled() is False  # missing key -> WhatsApp QR unavailable
    _env(monkeypatch, EVOLUTION_API_URL="")
    assert evolution_driver.enabled() is False  # missing url -> WhatsApp QR unavailable


def test_connect_creates_instance_and_returns_qr(evolution_client, monkeypatch):
    client = evolution_client
    _customer, line = _setup_line(client)
    calls = _stub_http(monkeypatch)
    response = client.post(f"/api/whatsapp/channels/{line['id']}/connect")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "qr"
    assert body["qr_code"] == "data:image/png;base64,QR1"
    assert any(path == f"/instance/connect/openlivery-{line['id']}" for _, path in calls)
    assert any(path == "/instance/create" for _, path in calls)


def test_connect_failure_marks_error(evolution_client, monkeypatch):
    client = evolution_client
    _customer, line = _setup_line(client)

    async def failing(method, path, *, json=None, timeout=30):
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="The Evolution API service is not available")

    monkeypatch.setattr(evolution_driver, "request", failing)
    response = client.post(f"/api/whatsapp/channels/{line['id']}/connect")
    assert response.status_code == 503
    refreshed = client.get(f"/api/whatsapp/channels/{line['id']}").json()
    assert refreshed["status"] == "error"
    assert "not available" in refreshed["last_error"]


def test_webhook_requires_token(client, monkeypatch):
    _env(monkeypatch)
    assert client.post("/api/public/whatsapp/evolution/webhook", json={"event": "x"}).status_code == 401
    assert client.post(
        "/api/public/whatsapp/evolution/webhook",
        json={"event": "x"}, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_webhook_qr_event_stores_the_code(authenticated_client, monkeypatch):
    client = authenticated_client
    _env(monkeypatch)
    _customer, line = _setup_line(client)
    qr = "data:image/png;base64,NEWQR"
    response = _webhook(client, _event(f"openlivery-{line['id']}",
                                       {"qrcode": {"base64": qr, "count": 1}}, "QRCODE_UPDATED"))
    assert response.status_code == 200
    updated = client.get(f"/api/whatsapp/channels/{line['id']}").json()
    assert updated["status"] == "qr"
    assert updated["qr_code"] == qr


def test_webhook_connection_open_connects(authenticated_client, monkeypatch):
    client = authenticated_client
    _env(monkeypatch)
    _customer, line = _setup_line(client)
    from app.routers import whatsapp_evolution as webhook_module

    monkeypatch.setattr(webhook_module, "_refresh_identity", AsyncMock())
    response = _webhook(client, _event(f"openlivery-{line['id']}", {"state": "open"}, "CONNECTION_UPDATE"))
    assert response.status_code == 200
    updated = client.get(f"/api/whatsapp/channels/{line['id']}").json()
    assert updated["status"] == "connected"
    assert updated["is_enabled"] is True


def test_webhook_connection_close_reconnects(authenticated_client, monkeypatch):
    client = authenticated_client
    _env(monkeypatch)
    _customer, line = _setup_line(client)
    response = _webhook(client, _event(f"openlivery-{line['id']}", {"state": "close"}, "CONNECTION_UPDATE"))
    assert response.status_code == 200
    updated = client.get(f"/api/whatsapp/channels/{line['id']}").json()
    assert updated["status"] == "reconnecting"


def test_webhook_inbound_message_replies(authenticated_client, monkeypatch):
    client = authenticated_client
    _env(monkeypatch)
    _customer, line = _setup_line(client)
    monkeypatch.setattr(whatsapp_inbound_service, "run_completion",
                        AsyncMock(return_value=ai_service.Completion(text="¡Hola! Sí, tenemos turnos.")))
    send_text = AsyncMock(return_value="wamid-evolution-reply-1")
    monkeypatch.setattr(evolution_driver, "send_text", send_text)
    response = _webhook(client, _event(
        f"openlivery-{line['id']}",
        {
            "key": {"remoteJid": "573001112233@s.whatsapp.net", "fromMe": False, "id": "wamid-in-9"},
            "pushName": "Maria",
            "message": {"conversation": "Hola, ¿tienen turnos?"},
            "messageType": "conversation",
        },
        "MESSAGES_UPSERT",
    ))
    assert response.status_code == 200
    conversation = client.get("/api/conversations").json()[0]
    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    assert detail["channel"] == "whatsapp"
    assert detail["contact_name"] == "Maria"
    roles = [item["role"] for item in detail["messages"]]
    assert roles == ["user", "assistant"]
    send_text.assert_awaited_once()


def test_webhook_inbound_from_me_mirrors_phone_message(authenticated_client, monkeypatch):
    client = authenticated_client
    _env(monkeypatch)
    _customer, line = _setup_line(client)
    response = _webhook(client, _event(
        f"openlivery-{line['id']}",
        {
            "key": {"remoteJid": "573001112233@s.whatsapp.net", "fromMe": True, "id": "wamid-me-1"},
            "message": {"conversation": "Thanks for reaching out!"},
        },
        "MESSAGES_UPSERT",
    ))
    assert response.status_code == 200
    conversation = client.get("/api/conversations").json()[0]
    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    roles = [item["role"] for item in detail["messages"] if item["role"] != "system"]
    assert roles == ["assistant"]
    assert detail["messages"][0]["content"] == "Thanks for reaching out!"
    assert detail["messages"][0]["sender_type"] == "human"


def test_webhook_inbound_media_message(authenticated_client, monkeypatch):
    client = authenticated_client
    _env(monkeypatch)
    _customer, line = _setup_line(client)
    monkeypatch.setattr(whatsapp_inbound_service, "run_completion",
                        AsyncMock(return_value=ai_service.Completion(text="Recibido.")))
    monkeypatch.setattr(evolution_driver, "send_text", AsyncMock(return_value="wamid-1"))
    response = _webhook(client, _event(
        f"openlivery-{line['id']}",
        {
            "key": {"remoteJid": "573001112233@s.whatsapp.net", "fromMe": False, "id": "wamid-img-1"},
            "message": {"imageMessage": {"base64": "aGVsbG8=", "mimetype": "image/jpeg", "caption": "logo?"}},
        },
        "MESSAGES_UPSERT",
    ))
    assert response.status_code == 200
    conversation = client.get("/api/conversations").json()[0]
    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    assert detail["messages"][0]["role"] == "user"


def test_webhook_ignores_foreign_instances(authenticated_client, monkeypatch):
    client = authenticated_client
    _env(monkeypatch)
    _customer, line = _setup_line(client)
    response = _webhook(client, _event("some-other-tool", {"state": "open"}, "CONNECTION_UPDATE"))
    assert response.status_code == 200
    updated = client.get(f"/api/whatsapp/channels/{line['id']}").json()
    assert updated["status"] == "disconnected"


def test_delete_line_removes_the_instance(evolution_client, monkeypatch):
    client = evolution_client
    _customer, line = _setup_line(client)
    calls = _stub_http(monkeypatch)
    response = client.delete(f"/api/whatsapp/channels/{line['id']}")
    assert response.status_code == 204
    assert any(path == f"/instance/delete/openlivery-{line['id']}" for _, path in calls)


def test_disconnect_line_logs_out(evolution_client, monkeypatch):
    client = evolution_client
    _customer, line = _setup_line(client)
    calls = _stub_http(monkeypatch)
    response = client.post(f"/api/whatsapp/channels/{line['id']}/disconnect")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "disconnected"
    assert body["has_session"] is False
    assert any(path == f"/instance/logout/openlivery-{line['id']}" for _, path in calls)


def test_connect_without_evolution_configured_fails(authenticated_client, monkeypatch):
    """No Evolution config: WhatsApp QR has no driver, so connecting fails clearly."""
    client = authenticated_client
    _env(monkeypatch, EVOLUTION_API_URL="", EVOLUTION_API_KEY="")
    _customer, line = _setup_line(client)
    response = client.post(f"/api/whatsapp/channels/{line['id']}/connect")
    assert response.status_code == 409


def test_send_media_voice_note_uses_ptv(monkeypatch):
    """Voice notes (ogg/opus) map to the ptv media type; images stay images."""
    _env(monkeypatch)
    seen = {}

    async def fake_request(method, path, *, json=None, timeout=30):
        seen["path"] = path
        seen["payload"] = json
        return {"key": {"id": "wamid-voice"}}

    monkeypatch.setattr(evolution_driver, "request", fake_request)
    channel = WhatsAppChannel(agency_id=uuid.uuid4(), client_id=uuid.uuid4(), agent_id=uuid.uuid4())
    result = asyncio.run(
        evolution_driver.send_media(channel, "573001112233@s.whatsapp.net",
                                    kind="audio", data=b"ogg-bytes", mime="audio/ogg")
    )
    assert result == "wamid-voice"
    assert seen["path"].startswith("/message/sendMedia/")
    assert seen["payload"]["mediatype"] == "ptv"


def test_settings_payload_reflects_toggles(authenticated_client, monkeypatch):
    from types import SimpleNamespace

    _env(monkeypatch)
    client = authenticated_client
    _customer, line = _setup_line(client)
    saved = client.put(f"/api/whatsapp/channels/{line['id']}", json={
        "agent_id": line["agent_id"], "groups_enabled": True, "calls_enabled": True,
        "calls_message": "We are closed right now"}).json()
    assert saved["groups_enabled"] is True and saved["calls_enabled"] is True
    assert saved["calls_message"] == "We are closed right now"
    payload = evolution_driver.settings_payload(SimpleNamespace(
        groups_enabled=True, calls_enabled=True, calls_message="We are closed right now"))
    assert payload["groupsIgnore"] is False and payload["rejectCall"] is False
    assert payload["msgCall"] == "We are closed right now"
    payload = evolution_driver.settings_payload(SimpleNamespace(
        groups_enabled=False, calls_enabled=False, calls_message=None))
    assert payload["groupsIgnore"] is True and payload["rejectCall"] is True and payload["msgCall"] == ""


def test_group_message_without_mention_is_ignored(authenticated_client, monkeypatch):
    client = authenticated_client
    _env(monkeypatch)
    _customer, line = _setup_line(client)
    client.put(f"/api/whatsapp/channels/{line['id']}", json={"agent_id": line["agent_id"], "groups_enabled": True})
    response = _webhook(client, _event(
        f"openlivery-{line['id']}",
        {"key": {"remoteJid": "120363@g.us", "fromMe": False, "id": "wamid-g1"},
         "pushName": "Ana", "message": {"conversation": "hola a todos"}},
        "MESSAGES_UPSERT",
    ))
    assert response.status_code == 200
    assert client.get("/api/conversations").json() == []


def test_group_message_with_mention_creates_group_conversation(authenticated_client, monkeypatch):
    client = authenticated_client
    _env(monkeypatch)
    calls = _stub_http(monkeypatch)
    _customer, line = _setup_line(client)
    client.put(f"/api/whatsapp/channels/{line['id']}", json={"agent_id": line["agent_id"], "groups_enabled": True})
    monkeypatch.setattr(whatsapp_inbound_service, "run_completion",
                        AsyncMock(return_value=ai_service.Completion(text="Hola grupo!")))
    monkeypatch.setattr(evolution_driver, "send_text", AsyncMock(return_value="wamid-g-reply"))
    response = _webhook(client, _event(
        f"openlivery-{line['id']}",
        {"key": {"remoteJid": "120363@g.us", "fromMe": False, "id": "wamid-g2"},
         "pushName": "Ana", "message": {"extendedTextMessage": {"text": "@openlivery hola"}},
         "messageType": "extendedTextMessage"},
        "MESSAGES_UPSERT",
    ))
    assert response.status_code == 200
    conversation = client.get("/api/conversations").json()[0]
    assert conversation["channel"] == "whatsapp"
    assert conversation["title"] == "Family group"


def test_group_message_when_disabled_is_ignored(authenticated_client, monkeypatch):
    client = authenticated_client
    _env(monkeypatch)
    _customer, line = _setup_line(client)
    response = _webhook(client, _event(
        f"openlivery-{line['id']}",
        {"key": {"remoteJid": "120363@g.us", "fromMe": False, "id": "wamid-g3"},
         "pushName": "Ana", "message": {"extendedTextMessage": {"text": "@openlivery hola"}}},
        "MESSAGES_UPSERT",
    ))
    assert response.status_code == 200
    assert client.get("/api/conversations").json() == []


def test_location_message_stores_attachment_and_coordinates(authenticated_client, monkeypatch):
    client = authenticated_client
    _env(monkeypatch)
    _customer, line = _setup_line(client)
    monkeypatch.setattr(whatsapp_inbound_service, "run_completion",
                        AsyncMock(return_value=ai_service.Completion(text="Ya vamos!")))
    monkeypatch.setattr(evolution_driver, "send_text", AsyncMock(return_value="wamid-loc-reply"))
    response = _webhook(client, _event(
        f"openlivery-{line['id']}",
        {"key": {"remoteJid": "573001112233@s.whatsapp.net", "fromMe": False, "id": "wamid-loc-1"},
         "pushName": "Maria",
         "message": {"locationMessage": {"degreesLatitude": 4.60971, "degreesLongitude": -74.08175,
                                          "name": "Office", "address": "Calle 10 #5-25"}}},
        "MESSAGES_UPSERT",
    ))
    assert response.status_code == 200
    conversation = client.get("/api/conversations").json()[0]
    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    visitor = next(item for item in detail["messages"] if item["role"] == "user")
    assert "Office" in visitor["content"]
    location_attachment = next(item for item in visitor["attachments"] if item["kind"] == "location")
    assert location_attachment["mime"] == "application/json"
    llm = [item for item in detail["messages"] if item["role"] == "assistant"]
    assert llm  # the AI answered


def test_document_message_stores_file_attachment(authenticated_client, monkeypatch):
    client = authenticated_client
    _env(monkeypatch)
    _customer, line = _setup_line(client)
    monkeypatch.setattr(whatsapp_inbound_service, "run_completion",
                        AsyncMock(return_value=ai_service.Completion(text="Recibido, gracias.")))
    monkeypatch.setattr(evolution_driver, "send_text", AsyncMock(return_value="wamid-doc-reply"))
    pdf_b64 = base64.b64encode(b"%PDF-1.4 test").decode()
    response = _webhook(client, _event(
        f"openlivery-{line['id']}",
        {"key": {"remoteJid": "573001112233@s.whatsapp.net", "fromMe": False, "id": "wamid-doc-1"},
         "pushName": "Maria",
         "message": {"documentMessage": {"base64": pdf_b64, "mimetype": "application/pdf",
                                          "fileName": "invoice.pdf", "caption": "here it is"}}},
        "MESSAGES_UPSERT",
    ))
    assert response.status_code == 200
    conversation = client.get("/api/conversations").json()[0]
    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    visitor = next(item for item in detail["messages"] if item["role"] == "user")
    file_attachment = next(item for item in visitor["attachments"] if item["kind"] == "file")
    assert file_attachment["filename"] == "invoice.pdf"


def test_send_location_endpoint_delivers_and_stores(authenticated_client, monkeypatch):
    client = authenticated_client
    _env(monkeypatch)
    _customer, line = _setup_line(client)
    send_location_mock = AsyncMock(return_value="wamid-loc-out-1")
    monkeypatch.setattr(evolution_driver, "send_location", send_location_mock)
    # A visitor conversation to reply into.
    _webhook(client, _event(
        f"openlivery-{line['id']}",
        {"key": {"remoteJid": "573001112233@s.whatsapp.net", "fromMe": False, "id": "wamid-seed-1"},
         "pushName": "Maria", "message": {"conversation": "where are you?"}},
        "MESSAGES_UPSERT",
    ))
    # An operator must take over before sending a location.
    conversation = client.get("/api/conversations").json()[0]
    client.patch(f"/api/conversations/{conversation['id']}/mode", json={"mode": "human"})
    response = client.post(f"/api/conversations/{conversation['id']}/location", json={
        "latitude": 4.60971, "longitude": -74.08175, "name": "Store", "address": "Calle 10"})
    assert response.status_code == 200
    detail = response.json()
    operator = next(item for item in detail["messages"] if item["role"] == "assistant" and item["sender_type"] == "human")
    assert operator["content"].startswith("\U0001F4CD")
    assert any(item["kind"] == "location" for item in operator["attachments"])
    send_location_mock.assert_awaited_once()


def test_send_location_requires_evolution(authenticated_client, monkeypatch):
    client = authenticated_client
    _env(monkeypatch, EVOLUTION_API_KEY="")
    _customer, line = _setup_line(client)
    _webhook(client, _event(
        f"openlivery-{line['id']}",
        {"key": {"remoteJid": "573001112233@s.whatsapp.net", "fromMe": False, "id": "wamid-seed-2"},
         "pushName": "Maria", "message": {"conversation": "hi"}},
        "MESSAGES_UPSERT",
    ))
    conversation = client.get("/api/conversations").json()[0]
    client.patch(f"/api/conversations/{conversation['id']}/mode", json={"mode": "human"})
    response = client.post(f"/api/conversations/{conversation['id']}/location", json={
        "latitude": 1.0, "longitude": 2.0})
    assert response.status_code == 409


def test_mark_read_and_react(monkeypatch):
    _env(monkeypatch)
    paths = []

    async def fake_request(method, path, *, json=None, timeout=30):
        paths.append((method, path, json))
        return {}

    monkeypatch.setattr(evolution_driver, "request", fake_request)
    channel = WhatsAppChannel(agency_id=uuid.uuid4(), client_id=uuid.uuid4(), agent_id=uuid.uuid4())
    asyncio.run(evolution_driver.mark_read(channel, "573001112233@s.whatsapp.net", ["m1", "m2"], typing=True))
    asyncio.run(evolution_driver.send_reaction(channel, "573001112233@s.whatsapp.net", "m1", "👍", target_from_me=False))
    read_calls = [item for item in paths if item[1].startswith("/chat/markMessageAsRead")]
    presence_calls = [item for item in paths if item[1].startswith("/chat/sendPresence")]
    reaction_calls = [item for item in paths if item[1].startswith("/message/sendReaction")]
    assert len(read_calls) == 1 and len(presence_calls) == 1 and len(reaction_calls) == 1
    assert read_calls[0][2]["readMessages"] == [
        {"remoteJid": "573001112233@s.whatsapp.net", "id": "m1", "fromMe": False},
        {"remoteJid": "573001112233@s.whatsapp.net", "id": "m2", "fromMe": False},
    ]
    assert reaction_calls[0][2]["key"]["id"] == "m1"
