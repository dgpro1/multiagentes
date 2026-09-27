"""The invitation an agency's first administrator receives, from issue to a
working account — including everything that must refuse it: expiry,
re-issuing, an e-mail already in use, and two simultaneous acceptances."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import Agency, AgencyAdminInvitation, User
from app.services.api_credentials import digest
from conftest import TestingSession
from test_platform_auth import _login, _platform_admin

ADMIN_EMAIL = "owner@agencia.example.com"
ADMIN_PASSWORD = "owner-password"


def _setup(client: TestClient, name="Agencia Sur") -> dict:
    _platform_admin()
    _login(client)
    response = client.post("/api/platform/agencies", json={
        "name": name, "admin_name": "Owner Sur", "admin_email": ADMIN_EMAIL,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_invitation_accept_creates_a_working_administrator(client):
    body = _setup(client)
    token = body["invitation"]["token"]
    info = client.get(f"/api/platform/invitations/{token}")
    assert info.status_code == 200
    assert info.json()["agency_slug"] == "agencia-sur"
    assert info.json()["status"] == "pending"
    accepted = client.post(f"/api/platform/invitations/{token}/accept", json={"password": ADMIN_PASSWORD})
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["email"] == ADMIN_EMAIL
    assert client.post(f"/api/platform/invitations/{token}/accept", json={"password": "otra"}).status_code == 410
    client.post("/api/auth/logout")
    login = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert login.status_code == 200, login.text
    assert login.json()["agency"]["name"] == "Agencia Sur"


def test_an_expired_invitation_is_refused(client):
    body = _setup(client)
    with TestingSession() as db:
        agency = db.scalar(select(Agency).where(Agency.slug == body["agency"]["slug"]))
        db.add(AgencyAdminInvitation(
            agency_id=agency.id, email="x@agencia.example.com", name="X",
            token_hash=digest("inv_viejo"), expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        ))
        db.commit()
    info = client.get("/api/platform/invitations/inv_viejo")
    assert info.status_code == 200 and info.json()["status"] == "expired"
    assert client.post("/api/platform/invitations/inv_viejo/accept", json={"password": "p"}).status_code == 410


def test_reissuing_revokes_the_previous_invitation(client):
    body = _setup(client)
    old = body["invitation"]["token"]
    reissued = client.post(
        f"/api/platform/agencies/{body['agency']['id']}/admin-invitations",
        json={"email": "otro@agencia.example.com", "name": "Otro"},
    )
    assert reissued.status_code == 201, reissued.text
    new = reissued.json()["token"]
    assert client.post(f"/api/platform/invitations/{old}/accept", json={"password": "p"}).status_code == 410
    assert client.post(f"/api/platform/invitations/{new}/accept", json={"password": "p"}).status_code == 201


def test_accept_refuses_an_email_that_already_has_an_account(client):
    body = _setup(client)
    with TestingSession() as db:
        agency = db.scalar(select(Agency).where(Agency.slug == body["agency"]["slug"]))
        db.add(User(agency=agency, name="Ocupado", email=ADMIN_EMAIL, password_hash="x"))
        db.commit()
    response = client.post(f"/api/platform/invitations/{body['invitation']['token']}/accept", json={"password": "p"})
    assert response.status_code == 409


def test_two_simultaneous_acceptances_create_one_administrator(client):
    body = _setup(client)
    token = body["invitation"]["token"]

    def accept():
        with TestClient(app) as requester:
            return requester.post(f"/api/platform/invitations/{token}/accept", json={"password": ADMIN_PASSWORD}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(lambda _index: accept(), range(2)))
    assert results == [201, 410]
    with TestingSession() as db:
        count = db.scalar(select(func.count()).select_from(User).where(User.email == ADMIN_EMAIL))
        assert count == 1
