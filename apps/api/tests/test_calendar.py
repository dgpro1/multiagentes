"""Google Calendar: calendar members, the public connection link, event
listing, and the agency/portal permission boundary. Google's HTTP endpoints
are mocked; app.services.calendar is exercised end to end."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.models import CalendarMember, now_utc
from app.security import decrypt_secret
from app.services import google_calendar as google
from conftest import TestingSession


@pytest.fixture(autouse=True)
def calendar_config(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.test")
    monkeypatch.setattr(settings, "google_client_id", "client-id.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_client_secret", "server-secret")
    monkeypatch.setattr(settings, "google_redirect_uri", "")


def _client_and_member(client: TestClient, name="Ana", role="Dentist"):
    customer = client.post("/api/clients", json={"name": "Dental Co", "is_active": True}).json()
    member = client.post(f"/api/clients/{customer['id']}/calendar/members", json={"name": name, "role": role}).json()
    return customer, member


def _connect_token(member: dict) -> str:
    return member["connect_url"].rsplit("/", 1)[-1]


def _grant(email="ana@gmail.com", scopes=None):
    scopes = scopes if scopes is not None else [google.EVENTS_SCOPE, google.FREEBUSY_SCOPE, "openid", "email"]
    return google.Grant(
        refresh_token="refresh-token-1",
        access_token="access-token-1",
        expires_at=now_utc() + timedelta(hours=1),
        email=email,
        scopes=frozenset(scopes),
    )


def test_create_member_and_connect_url(authenticated_client: TestClient):
    customer, member = _client_and_member(authenticated_client)
    assert member["status"] == "pending"
    assert member["name"] == "Ana" and member["role"] == "Dentist"
    assert member["connect_url"].startswith("https://app.example.test/connect/calendar/")
    overview = authenticated_client.get(f"/api/clients/{customer['id']}/calendar").json()
    assert overview["oauth_ready"] is True
    assert len(overview["members"]) == 1


def test_public_link_start_and_oauth_completion(authenticated_client: TestClient, monkeypatch):
    customer, member = _client_and_member(authenticated_client)
    token = _connect_token(member)

    # Unauthenticated: the public info and start endpoints need no session.
    anon = TestClient(authenticated_client.app)
    info = anon.get(f"/api/calendar/connect/{token}").json()
    assert info["member_name"] == "Ana" and info["status"] == "pending"

    started = anon.post(f"/api/calendar/connect/{token}/start")
    assert started.status_code == 200, started.text
    auth_url = started.json()["authorization_url"]
    parsed = urlsplit(auth_url)
    assert parsed.hostname == "accounts.google.com"
    query = parse_qs(parsed.query)
    assert query["client_id"] == ["client-id.apps.googleusercontent.com"]
    state = query["state"][0]

    monkeypatch.setattr(google, "exchange_code", AsyncMock(return_value=_grant()))
    callback = anon.get(f"/api/calendar/oauth/callback?state={state}&code=abc123", follow_redirects=False)
    assert callback.status_code in (302, 307)
    location = callback.headers["location"]
    assert location.startswith(f"https://app.example.test/connect/calendar/{token}")
    assert "result=connected" in location

    updated = authenticated_client.get(f"/api/clients/{customer['id']}/calendar").json()["members"][0]
    assert updated["status"] == "connected"
    assert updated["google_email"] == "ana@gmail.com"

    with TestingSession() as db:
        row = db.get(CalendarMember, member["id"])
        assert decrypt_secret(row.encrypted_refresh_token) == "refresh-token-1"


def test_oauth_state_is_single_use(authenticated_client: TestClient, monkeypatch):
    customer, member = _client_and_member(authenticated_client)
    token = _connect_token(member)
    anon = TestClient(authenticated_client.app)
    state = parse_qs(urlsplit(anon.post(f"/api/calendar/connect/{token}/start").json()["authorization_url"]).query)["state"][0]

    monkeypatch.setattr(google, "exchange_code", AsyncMock(return_value=_grant()))
    first = anon.get(f"/api/calendar/oauth/callback?state={state}&code=abc123", follow_redirects=False)
    assert "result=connected" in first.headers["location"]

    second = anon.get(f"/api/calendar/oauth/callback?state={state}&code=abc123", follow_redirects=False)
    assert "result=expired" in second.headers["location"]


def test_denied_or_missing_scope_leaves_member_disconnected(authenticated_client: TestClient, monkeypatch):
    customer, member = _client_and_member(authenticated_client)
    token = _connect_token(member)
    anon = TestClient(authenticated_client.app)

    state = parse_qs(urlsplit(anon.post(f"/api/calendar/connect/{token}/start").json()["authorization_url"]).query)["state"][0]
    denied = anon.get(f"/api/calendar/oauth/callback?state={state}&error=access_denied", follow_redirects=False)
    assert "result=denied" in denied.headers["location"]

    state2 = parse_qs(urlsplit(anon.post(f"/api/calendar/connect/{token}/start").json()["authorization_url"]).query)["state"][0]
    monkeypatch.setattr(google, "exchange_code", AsyncMock(return_value=_grant(scopes=["openid", "email"])))
    no_scope = anon.get(f"/api/calendar/oauth/callback?state={state2}&code=abc", follow_redirects=False)
    assert "result=scope" in no_scope.headers["location"]

    still_pending = authenticated_client.get(f"/api/clients/{customer['id']}/calendar").json()["members"][0]
    assert still_pending["status"] == "pending"


def test_renewing_the_link_invalidates_the_old_one(authenticated_client: TestClient):
    customer, member = _client_and_member(authenticated_client)
    old_token = _connect_token(member)
    renewed = authenticated_client.post(f"/api/clients/{customer['id']}/calendar/members/{member['id']}/renew-link").json()
    new_token = _connect_token(renewed)
    assert new_token != old_token
    anon = TestClient(authenticated_client.app)
    assert anon.get(f"/api/calendar/connect/{old_token}").status_code == 404
    assert anon.get(f"/api/calendar/connect/{new_token}").status_code == 200


def test_disconnect_clears_the_grant_and_revokes_it(authenticated_client: TestClient, monkeypatch):
    customer, member = _client_and_member(authenticated_client)
    token = _connect_token(member)
    anon = TestClient(authenticated_client.app)
    state = parse_qs(urlsplit(anon.post(f"/api/calendar/connect/{token}/start").json()["authorization_url"]).query)["state"][0]
    monkeypatch.setattr(google, "exchange_code", AsyncMock(return_value=_grant()))
    anon.get(f"/api/calendar/oauth/callback?state={state}&code=abc", follow_redirects=False)

    revoke = AsyncMock()
    monkeypatch.setattr(google, "revoke", revoke)
    disconnected = authenticated_client.post(f"/api/clients/{customer['id']}/calendar/members/{member['id']}/disconnect").json()
    assert disconnected["status"] == "pending"
    assert disconnected["google_email"] is None
    revoke.assert_awaited_once_with("refresh-token-1")


def test_deleting_a_client_takes_its_calendar_members_with_it(authenticated_client: TestClient):
    customer, member = _client_and_member(authenticated_client)
    with TestingSession() as db:
        assert db.get(CalendarMember, member["id"]) is not None
    authenticated_client.delete(f"/api/clients/{customer['id']}")
    with TestingSession() as db:
        assert db.get(CalendarMember, member["id"]) is None


def test_events_lists_connected_calendars_and_reports_errors(authenticated_client: TestClient, monkeypatch):
    customer, ana = _client_and_member(authenticated_client, "Ana", "Dentist")
    beto = authenticated_client.post(f"/api/clients/{customer['id']}/calendar/members", json={"name": "Beto", "role": "Hygienist"}).json()

    for member in (ana, beto):
        token = _connect_token(member)
        anon = TestClient(authenticated_client.app)
        state = parse_qs(urlsplit(anon.post(f"/api/calendar/connect/{token}/start").json()["authorization_url"]).query)["state"][0]
        grant = _grant(email=f"{member['name'].lower()}@gmail.com")
        grant = google.Grant(refresh_token=f"refresh-{member['name']}", access_token=f"access-{member['name']}", expires_at=grant.expires_at, email=grant.email, scopes=grant.scopes)
        monkeypatch.setattr(google, "exchange_code", AsyncMock(return_value=grant))
        anon.get(f"/api/calendar/oauth/callback?state={state}&code=abc", follow_redirects=False)

    async def fake_list_events(access_token, calendar_id, start, end):
        if access_token == "access-Ana":
            return [{
                "id": "evt1", "status": "confirmed", "summary": "Cleaning",
                "start": {"dateTime": "2026-01-05T15:00:00Z"}, "end": {"dateTime": "2026-01-05T15:30:00Z"},
            }]
        raise google.GoogleError("Google rejected the calendar access", revoked=True)

    monkeypatch.setattr(google, "list_events", fake_list_events)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 31, tzinfo=timezone.utc)
    result = authenticated_client.get(
        f"/api/clients/{customer['id']}/calendar/events",
        params={"start": start.isoformat(), "end": end.isoformat()},
    ).json()
    assert len(result["events"]) == 1
    assert result["events"][0]["title"] == "Cleaning"
    assert len(result["errors"]) == 1

    overview = authenticated_client.get(f"/api/clients/{customer['id']}/calendar").json()
    by_name = {m["name"]: m for m in overview["members"]}
    assert by_name["Beto"]["status"] == "error"
    assert by_name["Ana"]["status"] == "connected"


def test_events_rejects_naive_or_oversized_ranges(authenticated_client: TestClient):
    customer, _member = _client_and_member(authenticated_client)
    naive = authenticated_client.get(
        f"/api/clients/{customer['id']}/calendar/events",
        params={"start": "2026-01-01T00:00:00", "end": "2026-01-02T00:00:00"},
    )
    assert naive.status_code == 422

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    too_long = authenticated_client.get(
        f"/api/clients/{customer['id']}/calendar/events",
        params={"start": start.isoformat(), "end": (start + timedelta(days=90)).isoformat()},
    )
    assert too_long.status_code == 422


def test_portal_view_is_free_but_managing_needs_the_permission(authenticated_client: TestClient):
    customer = authenticated_client.post("/api/clients", json={"name": "Portal Calendar Co", "is_active": True}).json()
    slug = customer["portal_slug"]
    authenticated_client.post(
        f"/api/clients/{customer['id']}/portal-users",
        json={"name": "Ana", "email": f"ana@{slug}.com", "password": "secure-portal", "role": "agent"},
    )
    authenticated_client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_enabled": True})
    portal = TestClient(authenticated_client.app)
    portal.post(f"/api/portal/{slug}/login", json={"email": f"ana@{slug}.com", "password": "secure-portal"})

    assert portal.get(f"/api/portal/{slug}/calendar").status_code == 200
    denied = portal.post(f"/api/portal/{slug}/calendar/members", json={"name": "Cami", "role": "Vet"})
    assert denied.status_code == 403

    # An admin (the first portal user) can manage it.
    authenticated_client.post(
        f"/api/clients/{customer['id']}/portal-users",
        json={"name": "Owner", "email": f"owner@{slug}.com", "password": "secure-portal", "role": "admin"},
    )
    admin = TestClient(authenticated_client.app)
    admin.post(f"/api/portal/{slug}/login", json={"email": f"owner@{slug}.com", "password": "secure-portal"})
    created = admin.post(f"/api/portal/{slug}/calendar/members", json={"name": "Cami", "role": "Vet"})
    assert created.status_code == 201, created.text
