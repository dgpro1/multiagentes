"""An e-mail that opens several portals never signs into one silently: the
API answers a challenge naming each, and the app re-signs in naming one."""

from sqlalchemy.orm import Session

from app.models import Agency, Client, PortalUser
from app.security import hash_password
from conftest import TestingSession


def _seed_portal(db: Session, agency_name: str, client_name: str, email: str, password: str) -> None:
    agency = Agency(name=agency_name, slug=agency_name.lower().replace(" ", "-"))
    db.add(agency)
    db.flush()
    customer = Client(
        agency_id=agency.id,
        name=client_name,
        portal_slug=client_name.lower().replace(" ", "-"),
        portal_enabled=True,
        is_active=True,
    )
    db.add(customer)
    db.flush()
    db.add(PortalUser(
        client_id=customer.id, email=email, name="Shared owner",
        password_hash=hash_password(password), role="admin",
    ))


def _seed_two(client) -> None:
    with TestingSession() as db:
        _seed_portal(db, "Agencia Uno", "Cliente Uno", "shared@example.com", "shared-password")
        _seed_portal(db, "Agencia Dos", "Cliente Dos", "shared@example.com", "shared-password")
        db.commit()


def test_one_shared_email_two_portals_answers_a_challenge(client):
    _seed_two(client)
    response = client.post("/api/mobile/sign-in", json={"email": "shared@example.com", "password": "shared-password"})
    assert response.status_code == 409, response.text
    portals = response.json()["detail"]["portals"]
    assert {item["portal_slug"] for item in portals} == {"cliente-uno", "cliente-dos"}


def test_naming_the_portal_resolves_the_challenge(client):
    _seed_two(client)
    chosen = client.post("/api/mobile/sign-in", json={
        "email": "shared@example.com", "password": "shared-password", "portal_slug": "cliente-dos"})
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["portal_slug"] == "cliente-dos"


def test_a_single_match_still_signs_in_without_a_slug(client):
    with TestingSession() as db:
        _seed_portal(db, "Agencia Sola", "Cliente Sola", "solo@example.com", "solo-password")
        db.commit()
    signed = client.post("/api/mobile/sign-in", json={"email": "solo@example.com", "password": "solo-password"})
    assert signed.status_code == 200, signed.text
    assert signed.json()["portal_slug"] == "cliente-sola"


def test_a_wrong_password_never_names_portals(client):
    _seed_two(client)
    response = client.post("/api/mobile/sign-in", json={"email": "shared@example.com", "password": "wrong"})
    assert response.status_code == 401
