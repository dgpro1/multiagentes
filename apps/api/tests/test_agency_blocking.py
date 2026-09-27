"""Blocking an agency denies every one of its credentials — panel sessions
and logins, API tokens, the web portal, mobile sessions, OAuth grants and
pending invitations — and restoring brings them all back untouched, with each
change audited and the messaging deliberately left running."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.models import ApiIntegration, ApiToken
from app.security import create_portal_token
from app.services import api_credentials
from conftest import TestingSession
from test_platform_auth import _login, _platform_admin

ANA = {"email": "ana@prisma.com", "password": "contrasena-segura"}


def _platform(client: TestClient) -> None:
    _platform_admin()
    _login(client)


def _agency_id(client: TestClient) -> str:
    return client.get("/api/agency").json()["id"]


def _block(client: TestClient, agency_id: str, reason: str = "suspended") -> dict:
    response = client.put(f"/api/platform/agencies/{agency_id}/access", json={"status": "blocked", "reason": reason})
    assert response.status_code == 200, response.text
    return response.json()


def _unblock(client: TestClient, agency_id: str) -> None:
    assert client.put(f"/api/platform/agencies/{agency_id}/access", json={"status": "active"}).status_code == 200


def test_blocking_denies_the_panel_session_and_login_and_restoring_returns_them(authenticated_client):
    client = authenticated_client
    agency_id = _agency_id(client)
    _platform(client)
    body = _block(client, agency_id)
    assert body["access_status"] == "blocked"
    assert body["access_block_reason"] == "suspended"
    # The already-issued session stops working at its next request...
    assert client.get("/api/auth/me").status_code == 403
    assert client.get("/api/clients").status_code == 403
    # ...and login refuses too.
    assert client.post("/api/auth/login", json=ANA).status_code == 403
    _unblock(client, agency_id)
    assert client.post("/api/auth/login", json=ANA).status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_blocking_denies_api_tokens(authenticated_client):
    client = authenticated_client
    agency_id = _agency_id(client)
    customer = client.post("/api/clients", json={"name": "Acme", "is_active": True}).json()
    integration = client.post("/api/integrations", json={"name": "Reader", "preset": "read_only"}).json()
    issued = client.post(f"/api/integrations/{integration['id']}/tokens", json={"expires_in_days": 30}).json()
    headers = {"Authorization": f"Bearer {issued['token']}"}
    assert client.get(f"/api/clients/{customer['id']}", headers=headers).status_code == 200
    _platform(client)
    _block(client, agency_id)
    assert client.get(f"/api/clients/{customer['id']}", headers=headers).status_code == 403
    _unblock(client, agency_id)
    assert client.get(f"/api/clients/{customer['id']}", headers=headers).status_code == 200


def test_blocking_denies_the_web_portal(authenticated_client):
    from app.main import app
    from fastapi.testclient import TestClient as PortalClient

    client = authenticated_client
    agency_id = _agency_id(client)
    customer = client.post("/api/clients", json={"name": "Acme", "is_active": True}).json()
    # Enabling the portal needs someone who can sign in first.
    assert client.post(f"/api/clients/{customer['id']}/portal-users", json={
        "name": "Portal user", "email": "portal@acme.example.com", "password": "portal-password"}).status_code == 201
    assert client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_enabled": True}).status_code == 200

    def portal_get():
        requester = PortalClient(app)
        requester.cookies.set("portal_access_token", create_portal_token(customer["id"], customer["portal_slug"]))
        return requester.get(f"/api/portal/{customer['portal_slug']}/inbox")

    assert portal_get().status_code == 200
    _platform(client)
    _block(client, agency_id)
    assert portal_get().status_code == 403
    _unblock(client, agency_id)
    assert portal_get().status_code == 200


def test_blocking_denies_mobile_sign_in_and_sessions(authenticated_client):
    client = authenticated_client
    agency_id = _agency_id(client)
    customer = client.post("/api/clients", json={"name": "Acme", "is_active": True}).json()
    created = client.post(f"/api/clients/{customer['id']}/portal-users", json={
        "name": "Movil", "email": "movil@acme.example.com", "password": "movil-password"})
    assert created.status_code == 201, created.text
    assert client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_enabled": True}).status_code == 200
    signed = client.post("/api/mobile/sign-in", json={"email": "movil@acme.example.com", "password": "movil-password"})
    assert signed.status_code == 200, signed.text
    session_token = signed.json()["token"]
    _platform(client)
    _block(client, agency_id)
    assert client.post("/api/mobile/sign-in", json={"email": "movil@acme.example.com", "password": "movil-password"}).status_code == 403
    assert client.get("/api/mobile/session", headers={"Authorization": f"Bearer {session_token}"}).status_code == 403
    _unblock(client, agency_id)
    assert client.post("/api/mobile/sign-in", json={"email": "movil@acme.example.com", "password": "movil-password"}).status_code == 200


def test_blocking_refuses_pending_invitations(client):
    _platform(client)
    body = client.post("/api/platform/agencies", json={
        "name": "Norte", "admin_name": "Owner Norte", "admin_email": "owner@norte.example.com"}).json()
    token = body["invitation"]["token"]
    agency_id = body["agency"]["id"]
    _block(client, agency_id)
    assert client.post(f"/api/platform/invitations/{token}/accept", json={"password": "owner-password"}).status_code == 403
    _unblock(client, agency_id)
    assert client.post(f"/api/platform/invitations/{token}/accept", json={"password": "owner-password"}).status_code == 201


def test_blocking_denies_oauth_exchange_but_allows_revocation(authenticated_client):
    client = authenticated_client
    agency_id = _agency_id(client)
    integration = client.post("/api/integrations", json={"name": "Third", "preset": "read_only"}).json()
    oauth = client.post(f"/api/integrations/{integration['id']}/oauth-client", json={"redirect_uris": ["https://third.example/cb"]})
    assert oauth.status_code in (200, 201), oauth.text
    public_id = oauth.json()["oauth_client_id"]
    secret = oauth.json()["client_secret"]
    raw_code = "seeded-code"
    with TestingSession() as db:
        row = db.get(ApiIntegration, uuid.UUID(integration["id"]))
        db.add(ApiToken(
            integration_id=row.id, kind="auth_code", token_hash=api_credentials.digest(raw_code),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            redirect_uri="https://third.example/cb", scopes=["clients.read"],
        ))
        db.commit()
    exchange_payload = {
        "grant_type": "authorization_code", "client_id": public_id, "client_secret": secret,
        "code": raw_code, "redirect_uri": "https://third.example/cb",
    }
    _platform(client)
    _block(client, agency_id)
    exchange = client.post("/api/oauth/token", json=exchange_payload)
    assert exchange.status_code == 400, exchange.text
    assert exchange.json()["detail"]["error"] == "access_denied"
    # Revocation keeps working for a blocked agency, per RFC 7009 — it
    # consumes the code, so the blocked exchange never got one.
    assert client.post("/api/oauth/revoke", json={
        "client_id": public_id, "client_secret": secret, "token": raw_code}).status_code == 200
    _unblock(client, agency_id)
    # A fresh code for the same integration exchanges fine once unblocked.
    raw_code_two = "second-code"
    with TestingSession() as db:
        row = db.get(ApiIntegration, uuid.UUID(integration["id"]))
        db.add(ApiToken(
            integration_id=row.id, kind="auth_code", token_hash=api_credentials.digest(raw_code_two),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            redirect_uri="https://third.example/cb", scopes=["clients.read"],
        ))
        db.commit()
    again = client.post("/api/oauth/token", json={**exchange_payload, "code": raw_code_two})
    assert again.status_code == 200, again.text


def test_blocking_leaves_clients_and_audits_every_change(authenticated_client):
    client = authenticated_client
    agency_id = _agency_id(client)
    customer = client.post("/api/clients", json={"name": "Acme", "is_active": True}).json()
    _platform(client)
    _block(client, agency_id, reason="overdue payment")
    summary = client.get(f"/api/platform/agencies/{agency_id}/clients").json()
    assert summary[0]["is_active"] is True
    assert summary[0]["portal_slug"] == customer["portal_slug"]
    _unblock(client, agency_id)
    actions = [event["action"] for event in client.get("/api/platform/audit-events").json()]
    assert "agency.blocked" in actions
    assert "agency.unblocked" in actions
