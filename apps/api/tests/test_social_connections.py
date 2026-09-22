from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.config import get_settings
from app.models import Client, SocialChannel, SocialOAuthState, now_utc
from app.services import messaging_provider as provider_client
from app.services import social_connections as service
from conftest import TestingSession, login_legacy_owner


ACCOUNT = {"account_id": "abc123", "platform": "instagram", "username": "shop",
           "display_name": "Shop", "phone_number": None, "is_active": True,
           "profile_id": "prof-1", "raw": {}}


@pytest.fixture(autouse=True)
def messaging_config(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.test")
    monkeypatch.setattr(settings, "social_public_url", "https://app.example.test")
    monkeypatch.setattr(service, "_app_resolver", None)
    monkeypatch.setattr(service, "_connection_hooks", [])
    monkeypatch.setattr(service, "_state_hooks", [])
    from app.services import messaging_profiles as profiles

    async def _ensure(db, client):
        client.provider_profile_id = client.provider_profile_id or "prof-1"
        db.commit()
        return client.provider_profile_id

    monkeypatch.setattr(profiles, "ensure_client_profile", _ensure)
    monkeypatch.setattr(provider_client, "connect_url",
                        AsyncMock(return_value={"authorization_url": "https://hosted.example/connect/1"}))
    monkeypatch.setattr(provider_client, "list_accounts", AsyncMock(return_value=[ACCOUNT]))
    monkeypatch.setattr(provider_client, "require_account", AsyncMock(return_value=ACCOUNT))


def resources(client):
    customer = client.post("/api/clients", json={"name": "Shop", "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post("/api/agents", json={"client_id": customer["id"], "provider": "openrouter", "model": "gpt-4.1-mini", "name": "Support", "instructions": "", "personality": "", "is_active": True}).json()
    return customer, agent


def start(client, customer, agent):
    response = client.post("/api/social/instagram/oauth/start", json={"client_id": customer["id"], "agent_id": agent["id"]})
    assert response.status_code == 200, response.text
    return response.json()["authorization_url"]


def complete(client, customer, agent, account_id="abc123"):
    pending = client.get("/api/social/instagram/oauth/pending", params={"client_id": customer["id"]})
    assert pending.status_code == 200, pending.text
    setup = pending.json()["setup_id"]
    done = client.post("/api/social/instagram/oauth/complete",
                       json={"setup_id": setup, "external_account_id": account_id})
    assert done.status_code == 200, done.text
    return done.json()


def test_manual_credentials_are_retired(authenticated_client):
    client = authenticated_client
    customer, agent = resources(client)
    response = client.put(f"/api/social/instagram/channels/{customer['id']}", json={
        "agent_id": agent["id"], "external_account_id": "111", "app_id": "999",
        "access_token": "private-token", "app_secret": "private-secret"})
    assert response.status_code == 403


def test_oauth_start_returns_the_hosted_page_and_hides_secrets(authenticated_client):
    client = authenticated_client
    customer, agent = resources(client)
    url = start(client, customer, agent)
    assert url == "https://hosted.example/connect/1"
    with TestingSession() as db:
        state = db.scalar(select(SocialOAuthState))
        assert state is not None and state.provider == "instagram"


def test_complete_binds_the_account_and_lists_it(authenticated_client):
    client = authenticated_client
    customer, agent = resources(client)
    start(client, customer, agent)
    channel = complete(client, customer, agent)
    assert channel["status"] == "connected"
    assert channel["external_account_id"] == "abc123"
    assert channel["display_name"] == "Shop"
    assert channel["has_access_token"] is False
    assert channel["webhook_url"].endswith("/api/public/messaging/webhook")
    assert "secret" not in channel["webhook_url"]
    rows = client.get(f"/api/social/instagram/clients/{customer['id']}/channels").json()
    assert [row["external_account_id"] for row in rows] == ["abc123"]
    with TestingSession() as db:
        row = db.scalar(select(SocialChannel))
        assert row.encrypted_access_token is None and row.provider_profile_id == "prof-1"


def test_account_rebind_and_cross_client_assignment_are_rejected(authenticated_client):
    client = authenticated_client
    first, agent = resources(client)
    second, other_agent = resources(client)
    start(client, first, agent)
    created = complete(client, first, agent)
    assert created["external_account_id"] == "abc123"
    # An existing channel is never moved to a different account: completing
    # another account adds its own channel instead.
    with TestingSession() as db:
        from app.models import SocialChannel as SocialChannelModel
        from app.models import User
        user = db.scalar(select(User))
        row = db.scalar(select(SocialChannelModel).where(
            SocialChannelModel.client_id == first["id"]))
        import asyncio

        with pytest.raises(HTTPException):
            asyncio.run(service.connect_account(db, user, first["id"], agent["id"], "instagram",
                                                {"id": "other9"}, source="oauth", channel=row))
    # ...but the same account cannot serve two clients at once.
    start(client, second, other_agent)
    with TestingSession() as db:
        from app.models import Client as ClientModel
        other = db.scalar(select(ClientModel).where(ClientModel.id == second["id"]))
        other.provider_profile_id = "prof-2"
        db.commit()
    response = client.post("/api/social/instagram/oauth/complete",
                           json={"setup_id": "prof-2", "external_account_id": "abc123"})
    assert response.status_code == 409


def test_disconnecting_releases_the_account_for_another_client(authenticated_client):
    client = authenticated_client
    first, agent = resources(client)
    second, other_agent = resources(client)
    start(client, first, agent)
    complete(client, first, agent)
    assert client.post(f"/api/social/instagram/channels/{first['id']}/disconnect").status_code == 204
    assert client.get(f"/api/social/instagram/channels/{first['id']}").status_code == 404
    with TestingSession() as db:
        from app.models import Client as ClientModel
        second_row = db.scalar(select(ClientModel).where(ClientModel.name == "Shop", ClientModel.id != first["id"]))
        second_row.provider_profile_id = "prof-2"
        db.commit()
    start(client, second, other_agent)
    moved = client.post("/api/social/instagram/oauth/complete",
                        json={"setup_id": "prof-2", "external_account_id": "abc123"})
    assert moved.status_code == 200, moved.text
    with TestingSession() as db:
        rows = db.scalars(select(SocialChannel).where(SocialChannel.external_account_id == "abc123")).all()
        assert [str(row.client_id) for row in rows] == [second["id"]]


def test_another_agency_cannot_read_or_change_channel(authenticated_client):
    client = authenticated_client
    customer, agent = resources(client)
    start(client, customer, agent)
    complete(client, customer, agent)
    login_legacy_owner(client)
    assert client.get(f"/api/social/instagram/channels/{customer['id']}").status_code == 404


def test_oauth_rejects_unsafe_return_path(authenticated_client):
    client = authenticated_client
    customer, agent = resources(client)
    base = {"client_id": customer["id"], "agent_id": agent["id"]}
    assert client.post("/api/social/instagram/oauth/start", json={**base, "next_path": "//evil.test"}).status_code == 400


def test_callback_binds_the_approved_account(authenticated_client, monkeypatch):
    client = authenticated_client
    customer, agent = resources(client)
    start(client, customer, agent)
    response = client.get("/api/public/messaging/connect/callback",
                          params={"connected": "instagram", "profileId": "prof-1", "accountId": "abc123"},
                          follow_redirects=False)
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert "messaging_status=ready" in location
    with TestingSession() as db:
        row = db.scalar(select(SocialChannel))
        assert row.external_account_id == "abc123" and row.status == "connected"
        assert f"line={row.id}" in location


def test_callback_error_lands_with_status(authenticated_client):
    client = authenticated_client
    customer, agent = resources(client)
    start(client, customer, agent)
    response = client.get("/api/public/messaging/connect/callback",
                          params={"error": "access_denied", "profileId": "prof-1"},
                          follow_redirects=False)
    assert response.status_code == 303, response.text
    assert "messaging_status=error" in response.headers["location"]
    with TestingSession() as db:
        assert db.scalar(select(SocialChannel)) is None


def test_disconnect_removes_the_channel(authenticated_client):
    client = authenticated_client
    customer, agent = resources(client)
    start(client, customer, agent)
    complete(client, customer, agent)
    unlinked = []
    service.register_connection_hook(lambda db, channel, event: unlinked.append(event))
    response = client.post(f"/api/social/instagram/channels/{customer['id']}/disconnect")
    assert response.status_code == 204
    assert unlinked == ["unlinked"]
    assert client.get(f"/api/social/instagram/channels/{customer['id']}").status_code == 404


def test_refresh_confirms_and_flags_missing_accounts(authenticated_client, monkeypatch):
    import asyncio

    client = authenticated_client
    customer, agent = resources(client)
    start(client, customer, agent)
    complete(client, customer, agent)
    with TestingSession() as db:
        channel = db.scalar(select(SocialChannel))
        asyncio.run(service.refresh_due_channels(db))
        assert channel.last_error is None
        monkeypatch.setattr(provider_client, "require_account",
                            AsyncMock(side_effect=HTTPException(404, "gone")))
        channel.token_refresh_attempted_at = None
        db.commit()
        asyncio.run(service.refresh_due_channels(db))
        assert channel.status == "error"


def test_refresh_scrubs_expired_states(authenticated_client):
    import asyncio
    from datetime import timedelta

    client = authenticated_client
    customer, agent = resources(client)
    start(client, customer, agent)
    with TestingSession() as db:
        state = db.scalar(select(SocialOAuthState))
        state.expires_at = now_utc() - timedelta(seconds=1)
        state_id = state.id
        db.commit()
        asyncio.run(service.refresh_due_channels(db))
        assert db.get(SocialOAuthState, state_id) is None
