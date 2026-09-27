"""Provisioning the platform closes public first-run setup, even before the
first agency exists."""

from app.models import PlatformAdmin
from app.security import hash_password
from conftest import TestingSession


def test_a_platform_admin_closes_public_registration(client):
    assert client.get("/api/auth/status").json() == {"needs_setup": True, "registration_open": True}
    with TestingSession() as db:
        db.add(PlatformAdmin(name="Owner", email="owner@example.com", password_hash=hash_password("x")))
        db.commit()
    assert client.get("/api/auth/status").json() == {"needs_setup": False, "registration_open": False}
    response = client.post("/api/auth/register", json={
        "agency_name": "Sneaky", "name": "Sneaky Owner", "email": "sneaky@example.com", "password": "password",
    })
    assert response.status_code == 403
