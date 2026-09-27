"""The platform session is its own identity: sign-in, revocation, and no
token of any other domain resolving as it (or it as any of them)."""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient

from app.config import get_settings
from app.models import PlatformAdmin
from app.security import create_platform_token, create_portal_token, hash_password
from conftest import TestingSession

PLATFORM = {"email": "owner@example.com", "password": "platform-password"}


def _platform_admin(**overrides) -> PlatformAdmin:
    with TestingSession() as db:
        admin = PlatformAdmin(
            name="Platform owner",
            email=PLATFORM["email"],
            password_hash=hash_password(PLATFORM["password"]),
            **overrides,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        return admin


def _login(client: TestClient):
    response = client.post("/api/platform/auth/login", json={"email": PLATFORM["email"], "password": PLATFORM["password"]})
    assert response.status_code == 200, response.text
    return response.json()


def test_login_me_and_logout(client):
    _platform_admin()
    assert _login(client)["email"] == PLATFORM["email"]
    me = client.get("/api/platform/auth/me")
    assert me.status_code == 200 and me.json()["name"] == "Platform owner"
    assert client.post("/api/platform/auth/logout").status_code == 204
    assert client.get("/api/platform/auth/me").status_code == 401


def test_wrong_password_and_disabled_account_are_refused(client):
    admin = _platform_admin()
    response = client.post("/api/platform/auth/login", json={"email": PLATFORM["email"], "password": "wrong"})
    assert response.status_code == 401
    with TestingSession() as db:
        row = db.get(PlatformAdmin, admin.id)
        row.is_active = False
        db.commit()
    assert client.post(
        "/api/platform/auth/login", json={"email": PLATFORM["email"], "password": PLATFORM["password"]}
    ).status_code == 401


def test_a_session_version_bump_revokes_every_session(client):
    admin = _platform_admin()
    _login(client)
    assert client.get("/api/platform/auth/me").status_code == 200
    with TestingSession() as db:
        row = db.get(PlatformAdmin, admin.id)
        row.session_version += 1
        db.commit()
    assert client.get("/api/platform/auth/me").status_code == 401


def test_agency_and_platform_cookies_do_not_leak_into_each_other(authenticated_client):
    client = authenticated_client
    _platform_admin()
    _login(client)
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/platform/auth/me").status_code == 200
    client.post("/api/platform/auth/logout")
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/platform/auth/me").status_code == 401
    _login(client)
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/platform/auth/me").status_code == 200


def test_a_platform_token_does_not_resolve_as_an_agency_session(authenticated_client):
    client = authenticated_client
    admin = _platform_admin()
    client.cookies.set("access_token", create_platform_token(str(admin.id), admin.session_version))
    assert client.get("/api/auth/me").status_code == 401


def test_an_agency_token_does_not_resolve_as_a_platform_session(authenticated_client):
    client = authenticated_client
    _platform_admin()
    client.cookies.set("platform_access_token", client.cookies.get("access_token"))
    assert client.get("/api/platform/auth/me").status_code == 401


def test_a_portal_token_is_no_longer_an_agency_session(authenticated_client):
    client = authenticated_client
    client.cookies.set("access_token", create_portal_token(str(uuid.uuid4()), "some-portal"))
    assert client.get("/api/auth/me").status_code == 401


def test_a_legacy_untyped_token_still_works_as_an_agency_session(authenticated_client):
    client = authenticated_client
    me_id = client.get("/api/auth/me").json()["id"]
    legacy = jwt.encode(
        {"sub": me_id, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        get_settings().secret_key,
        algorithm="HS256",
    )
    client.cookies.set("access_token", legacy)
    assert client.get("/api/auth/me").status_code == 200


def test_the_same_uuid_in_both_tables_resolves_per_domain(authenticated_client):
    client = authenticated_client
    agency_me = client.get("/api/auth/me").json()
    _platform_admin(id=uuid.UUID(agency_me["id"]))
    _login(client)
    assert client.get("/api/auth/me").json()["id"] == agency_me["id"]
    assert client.get("/api/platform/auth/me").json()["id"] == agency_me["id"]


def test_an_api_token_has_no_platform_surface(client):
    _platform_admin()
    assert client.get("/api/platform/auth/me", headers={"Authorization": "Bearer ol_nonsense"}).status_code == 401
