"""Services: the products and services a client's business offers.

Managed from two doors: the agency's client page and the client's own portal
(behind the ``services`` portal function, off by default), over the same rows.
These tests verify validation rules, agency boundary, client isolation,
and portal function and permission checks.
"""

import uuid
import pytest
from fastapi.testclient import TestClient

from app.models import Client, Service
from conftest import TestingSession, login_legacy_owner

NO_FEATURE = {"detail": "This feature is not enabled for this portal"}
FORBIDDEN = {"detail": "Your role cannot do this"}
ZERO = "00000000-0000-0000-0000-000000000000"


def _customer(client: TestClient, name: str = "Dental Co") -> dict:
    return client.post("/api/clients", json={"name": name, "is_active": True}).json()


def _url(customer: dict) -> str:
    return f"/api/clients/{customer['id']}/services"


def _portal(client: TestClient, name: str = "Portal Co", on: bool = True) -> tuple[dict, str]:
    customer = _customer(client, name)
    slug = customer["portal_slug"]
    body = {"name": "Ana", "email": f"ana@{slug}.com", "password": "secure-portal"}
    assert client.post(f"/api/clients/{customer['id']}/portal-users", json=body).status_code == 201
    assert client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_enabled": True}).status_code == 200
    if on:
        assert client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_features": {"services": True}}).status_code == 200
    assert client.post(f"/api/portal/{slug}/login", json={"email": body["email"], "password": "secure-portal"}).status_code == 200
    return customer, f"/api/portal/{slug}/services"


def _agent_session(client: TestClient, customer: dict) -> TestClient:
    slug = customer["portal_slug"]
    body = {"name": "Beto", "email": f"beto@{slug}.com", "password": "secure-portal", "role": "agent"}
    assert client.post(f"/api/clients/{customer['id']}/portal-users", json=body).status_code == 201
    other = TestClient(client.app)
    assert other.post(f"/api/portal/{slug}/login", json={"email": body["email"], "password": "secure-portal"}).status_code == 200
    return other


# --- Agency Endpoints ---


def test_the_agency_creates_lists_updates_and_deletes_services(authenticated_client: TestClient):
    client = authenticated_client
    customer = _customer(client)
    assert client.get(_url(customer)).json() == []

    payload = {
        "name": " Limpieza Dental ",
        "description": "Limpieza profunda con ultrasonido.",
        "price": 50.0,
        "currency": "USD",
        "duration_minutes": 45,
        "modality": "presencial",
        "requires_deposit": True,
        "deposit_amount": 15.0,
        "requirements": "Venir en ayunas de 2 horas.",
        "is_active": True,
        "position": 1,
    }
    created = client.post(_url(customer), json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["client_id"] == customer["id"]
    assert body["name"] == "Limpieza Dental"
    assert body["description"] == "Limpieza profunda con ultrasonido."
    assert body["price"] == 50.0
    assert body["currency"] == "USD"
    assert body["duration_minutes"] == 45
    assert body["modality"] == "presencial"
    assert body["requires_deposit"] is True
    assert body["deposit_amount"] == 15.0
    assert body["requirements"] == "Venir en ayunas de 2 horas."
    assert body["is_active"] is True
    assert body["position"] == 1

    listed = client.get(_url(customer)).json()
    assert len(listed) == 1
    assert listed[0]["id"] == body["id"]

    # Update
    updated = client.patch(
        f"{_url(customer)}/{body['id']}",
        json={
            "name": "Limpieza Dental Premium",
            "price": 60.0,
            "duration_minutes": 60,
            "requires_deposit": False,
        },
    )
    assert updated.status_code == 200, updated.text
    after = updated.json()
    assert after["name"] == "Limpieza Dental Premium"
    assert after["price"] == 60.0
    assert after["duration_minutes"] == 60
    assert after["requires_deposit"] is False
    assert after["deposit_amount"] is None

    # Delete
    assert client.delete(f"{_url(customer)}/{body['id']}").status_code == 204
    assert client.get(_url(customer)).json() == []
    with TestingSession() as db:
        assert db.get(Service, uuid.UUID(body["id"])) is None


def test_service_validation_rules(authenticated_client: TestClient):
    client = authenticated_client
    customer = _customer(client)

    # Empty name rejected
    assert client.post(_url(customer), json={"name": "   "}).status_code == 422

    # Invalid modality rejected
    assert client.post(_url(customer), json={"name": "Test", "modality": "telepatico"}).status_code == 422

    # Valid modalities accepted
    for modality in ("presencial", "online", "a_domicilio"):
        res = client.post(_url(customer), json={"name": f"Test {modality}", "modality": modality})
        assert res.status_code == 201, res.text

    # Deposit exceeding price rejected
    bad_deposit = client.post(
        _url(customer),
        json={"name": "Test Deposit", "price": 50.0, "requires_deposit": True, "deposit_amount": 100.0},
    )
    assert bad_deposit.status_code == 422


def test_client_business_location_and_hours(authenticated_client: TestClient):
    client = authenticated_client
    customer = _customer(client)

    hours = {
        "mon": [["09:00", "13:00"], ["14:00", "18:00"]],
        "tue": [["09:00", "18:00"]],
        "wed": [],
        "thu": [],
        "fri": [["08:00", "16:00"]],
        "sat": [],
        "sun": [],
    }

    # Update via agency
    patch_res = client.patch(
        f"/api/clients/{customer['id']}",
        json={
            "address": "Calle 100 # 15-20, Bogotá",
            "google_maps_url": "https://maps.google.com/?q=4.6,-74.0",
            "business_hours": hours,
        },
    )
    assert patch_res.status_code == 200, patch_res.text
    patched = patch_res.json()
    assert patched["address"] == "Calle 100 # 15-20, Bogotá"
    assert patched["google_maps_url"] == "https://maps.google.com/?q=4.6,-74.0"
    assert patched["business_hours"]["mon"] == [["09:00", "13:00"], ["14:00", "18:00"]]
    assert patched["business_hours"]["fri"] == [["08:00", "16:00"]]

    # Null clearing
    clear_res = client.patch(
        f"/api/clients/{customer['id']}",
        json={"address": None, "google_maps_url": None, "business_hours": None},
    )
    assert clear_res.status_code == 200, clear_res.text
    cleared = clear_res.json()
    assert cleared["address"] is None
    assert cleared["google_maps_url"] is None
    assert cleared["business_hours"] is None


# --- Portal Endpoints ---


def test_portal_services_feature_gating_and_permissions(authenticated_client: TestClient):
    client = authenticated_client
    customer, portal_url = _portal(client, on=False)

    # Feature is OFF -> 403
    assert client.get(portal_url).status_code == 403
    assert client.get(portal_url).json() == NO_FEATURE
    assert client.post(portal_url, json={"name": "S1"}).status_code == 403

    # Switch feature ON
    client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_features": {"services": True}})
    assert client.get(portal_url).status_code == 200
    assert client.get(portal_url).json() == []

    # Admin creates service via portal
    created = client.post(portal_url, json={"name": "Consulta General", "price": 30.0, "duration_minutes": 30})
    assert created.status_code == 201, created.text
    sid = created.json()["id"]

    # Admin updates service via portal
    updated = client.patch(f"{portal_url}/{sid}", json={"price": 35.0})
    assert updated.status_code == 200
    assert updated.json()["price"] == 35.0

    # Agent role session
    agent_client = _agent_session(client, customer)
    # Agent can list services
    assert len(agent_client.get(portal_url).json()) == 1

    # Agent cannot write (create, update, delete) -> 403 FORBIDDEN
    assert agent_client.post(portal_url, json={"name": "No Permission"}).status_code == 403
    assert agent_client.post(portal_url, json={"name": "No Permission"}).json() == FORBIDDEN
    assert agent_client.patch(f"{portal_url}/{sid}", json={"price": 40.0}).status_code == 403
    assert agent_client.delete(f"{portal_url}/{sid}").status_code == 403

    # Admin deletes service via portal
    assert client.delete(f"{portal_url}/{sid}").status_code == 204
    assert client.get(portal_url).json() == []


def test_agency_and_client_isolation(authenticated_client: TestClient):
    client = authenticated_client
    c1 = _customer(client, "C1")
    c2 = _customer(client, "C2")

    s1 = client.post(_url(c1), json={"name": "Service 1"}).json()

    # Agency cannot access c1's service under c2 URL
    assert client.get(f"/api/clients/{c2['id']}/services").json() == []
    assert client.patch(f"/api/clients/{c2['id']}/services/{s1['id']}", json={"name": "Hacked"}).status_code == 404
    assert client.delete(f"/api/clients/{c2['id']}/services/{s1['id']}").status_code == 404

    # Other agency cannot see or touch c1's services
    login_legacy_owner(client)
    assert client.get(_url(c1)).status_code == 404
    assert client.post(_url(c1), json={"name": "Other"}).status_code == 404
    assert client.patch(f"{_url(c1)}/{s1['id']}", json={"name": "Other"}).status_code == 404
    assert client.delete(f"{_url(c1)}/{s1['id']}").status_code == 404
