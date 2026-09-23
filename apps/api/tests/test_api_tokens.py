"""The API credentials: issuing them, what they reach and what they may not.

A token is a secret handed to a third party, so every gap in what it can touch
is a data leak rather than a bug. These tests hold a token to its agency, its
client and its scopes, and check that a cookie session is untouched by any of
it.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from conftest import TestingSession

from app import config
from app.models import ApiToken
from app.ratelimit import RateLimiter


def _integration(client, **overrides) -> dict:
    payload = {"name": "Zapier", "preset": "full"}
    payload.update(overrides)
    response = client.post("/api/integrations", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _issue(client, integration_id: str, *, days: int = 30) -> dict:
    response = client.post(f"/api/integrations/{integration_id}/tokens", json={"expires_in_days": days})
    assert response.status_code == 201, response.text
    return response.json()


def _customer(client, name: str) -> dict:
    response = client.post("/api/clients", json={"name": name, "is_active": True})
    assert response.status_code == 201, response.text
    return response.json()


def test_a_token_reaches_the_routes_it_declares(authenticated_client):
    """The whole point: a secret instead of a session reaches the same API."""
    client = authenticated_client
    _customer(client, "Acme")
    issued = _issue(client, _integration(client)["id"])

    listed = client.get("/api/clients", headers={"Authorization": f"Bearer {issued['token']}"})
    assert listed.status_code == 200, listed.text
    assert [row["name"] for row in listed.json()] == ["Acme"]
    # The same credential in the header a Kommo-style client would send.
    assert client.get("/api/clients", headers={"X-API-Key": issued["token"]}).status_code == 200


def test_the_secret_is_shown_once(authenticated_client):
    client = authenticated_client
    integration = _integration(client)
    issued = _issue(client, integration["id"])

    assert issued["token"].startswith("ol_")
    listed = client.get(f"/api/integrations/{integration['id']}/tokens").json()
    assert len(listed) == 1
    assert listed[0]["token_prefix"] == issued["token_prefix"]
    assert issued["token"] not in str(listed)


def test_a_revoked_token_stops_working(authenticated_client):
    client = authenticated_client
    integration = _integration(client)
    issued = _issue(client, integration["id"])
    headers = {"Authorization": f"Bearer {issued['token']}"}
    assert client.get("/api/clients", headers=headers).status_code == 200

    row_id = client.get(f"/api/integrations/{integration['id']}/tokens").json()[0]["id"]
    assert client.delete(f"/api/integrations/{integration['id']}/tokens/{row_id}").status_code == 204
    assert client.get("/api/clients", headers=headers).status_code == 401
    # A token nobody issued is refused the same way.
    assert client.get("/api/clients", headers={"Authorization": "Bearer ol_not-a-token"}).status_code == 401


def test_an_expired_token_is_refused(authenticated_client):
    client = authenticated_client
    integration = _integration(client)
    issued = _issue(client, integration["id"])
    with TestingSession() as db:
        token = db.scalar(select(ApiToken))
        token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    refused = client.get("/api/clients", headers={"Authorization": f"Bearer {issued['token']}"})
    assert refused.status_code == 401
    assert "expired" in refused.json()["detail"].lower()


def test_a_missing_scope_is_named_in_the_refusal(authenticated_client):
    client = authenticated_client
    reader = _integration(client, name="Reader", scopes=["clients.read"])
    headers = {"Authorization": f"Bearer {_issue(client, reader['id'])['token']}"}

    assert client.get("/api/clients", headers=headers).status_code == 200
    refused = client.post("/api/clients", json={"name": "Nope", "is_active": True}, headers=headers)
    assert refused.status_code == 403
    assert "clients.write" in refused.json()["detail"]


def test_a_route_that_declares_no_scopes_stays_closed(authenticated_client):
    """A route nobody has annotated is out of reach for a token, so the surface
    a secret can touch grows on purpose instead of by accident. The agency's own
    settings are the standing example: they are a person's business."""
    client = authenticated_client
    headers = {"Authorization": f"Bearer {_issue(client, _integration(client)['id'])['token']}"}

    refused = client.get("/api/providers", headers=headers)
    assert refused.status_code == 403
    assert "not available to API tokens" in refused.json()["detail"]


def test_credentials_need_their_own_scope(authenticated_client):
    client = authenticated_client
    narrow = _integration(client, name="Partner", preset="read_only")
    holder = _integration(client, name="Holder", preset="full")

    refuse = {"Authorization": f"Bearer {_issue(client, narrow['id'])['token']}"}
    assert client.get("/api/integrations", headers=refuse).status_code == 403

    allowed = {"Authorization": f"Bearer {_issue(client, holder['id'])['token']}"}
    assert client.get("/api/integrations", headers=allowed).status_code == 200
    assert client.get("/api/integrations/scopes", headers=allowed).status_code == 200


def test_an_unknown_scope_is_rejected(authenticated_client):
    client = authenticated_client
    response = client.post("/api/integrations", json={"name": "Typo", "scopes": ["clients.reed"]})
    assert response.status_code == 400
    assert "clients.reed" in response.json()["detail"]


def test_a_client_limited_token_stays_inside_its_client(authenticated_client):
    client = authenticated_client
    mine = _customer(client, "Mine")
    other = _customer(client, "Other")
    limited = _integration(client, name="Limited", client_id=mine["id"], scopes=["clients.read", "pipeline.read"])
    headers = {"Authorization": f"Bearer {_issue(client, limited['id'])['token']}"}

    assert client.get(f"/api/clients/{mine['id']}/pipeline/board", headers=headers).status_code == 200
    assert client.get(f"/api/clients/{other['id']}/pipeline/board", headers=headers).status_code == 403
    # A route that names no client at all is out of reach for a limited token.
    assert client.get("/api/clients", headers=headers).status_code == 403


def test_a_person_signing_in_is_unchanged(authenticated_client):
    """Everything keeps working on a cookie, including the routes a token may
    not call."""
    client = authenticated_client
    assert client.get("/api/agents").status_code == 200
    assert client.get("/api/integrations").status_code == 200
    assert client.get("/api/integrations/scopes").json()["presets"]["full"]


def test_the_credential_gets_its_own_request_budget(monkeypatch):
    """Kommo's seven per second, counted per credential so a shared office
    address cannot throttle a well-behaved integration."""
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    config.get_settings.cache_clear()
    limiter = RateLimiter(7, 1, name="api-token-budget")
    try:
        for _ in range(7):
            limiter.check("token-a")
        with pytest.raises(HTTPException) as refused:
            limiter.check("token-a")
        assert refused.value.status_code == 429
        assert refused.value.headers["Retry-After"]
        # Another credential has its own window.
        limiter.check("token-b")
    finally:
        config.get_settings.cache_clear()
