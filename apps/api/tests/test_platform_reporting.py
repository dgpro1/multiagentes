"""The platform's overview, per-agency usage and infrastructure health, all
read from the control plane and scoped to the agency named in the URL."""

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import Agency, Client, ClientDataStore, ClientStorageConnection, UsageRecord
from app.security import encrypt_secret
from conftest import TestingSession
from test_platform_auth import _login, _platform_admin


def _admin(client) -> None:
    _platform_admin()
    _login(client)


def _create(client, name, slug, email):
    response = client.post("/api/platform/agencies", json={
        "name": name, "slug": slug, "admin_name": "Owner", "admin_email": email,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_overview_counts_everything_and_reports_usage(client):
    _admin(client)
    _create(client, "Agencia A", "agencia-a", "a@a.example.com")
    _create(client, "Agencia B", "agencia-b", "b@b.example.com")
    with TestingSession() as db:
        agency = db.scalar(select(Agency).where(Agency.slug == "agencia-a"))
        db.add(Client(agency_id=agency.id, name="Cliente", portal_slug="cliente-a"))
        db.add(UsageRecord(agency_id=agency.id, provider="openrouter", model="openai/gpt-5.6-luna",
                           input_tokens=100, output_tokens=50, cost_usd=0.01))
        db.add(UsageRecord(agency_id=agency.id, provider="openrouter", model="openai/gpt-5.6-luna",
                           input_tokens=10, output_tokens=5))
        db.commit()
    overview = client.get("/api/platform/overview")
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["agencies"] == 2
    assert body["clients"] == 1
    assert body["usage"]["replies"] == 2
    assert body["usage"]["input_tokens"] == 110
    assert body["usage"]["cost_usd"] == 0.01
    assert body["usage"]["unpriced_replies"] == 1
    assert any(event["action"] == "agency.created" for event in body["recent_events"])


def test_usage_is_scoped_to_the_agency(client):
    _admin(client)
    a = _create(client, "Agencia A", "agencia-a", "a@a.example.com")
    b = _create(client, "Agencia B", "agencia-b", "b@b.example.com")
    with TestingSession() as db:
        agency_a = db.scalar(select(Agency).where(Agency.slug == "agencia-a"))
        agency_b = db.scalar(select(Agency).where(Agency.slug == "agencia-b"))
        db.add(UsageRecord(agency_id=agency_a.id, provider="openrouter", model="model-a", input_tokens=1, output_tokens=1))
        db.add(UsageRecord(agency_id=agency_b.id, provider="openrouter", model="model-b", input_tokens=2, output_tokens=2))
        db.commit()
    usage_a = client.get(f"/api/platform/agencies/{a['agency']['id']}/usage")
    assert usage_a.status_code == 200, usage_a.text
    assert usage_a.json()["total"]["replies"] == 1
    assert len(usage_a.json()["days"]) == 1
    usage_b = client.get(f"/api/platform/agencies/{b['agency']['id']}/usage")
    assert usage_b.json()["total"]["replies"] == 1
    assert client.get("/api/platform/agencies/00000000-0000-0000-0000-000000000000/usage").status_code == 404


def test_infrastructure_lists_every_client_of_the_agency(client):
    _admin(client)
    a = _create(client, "Agencia A", "agencia-a", "a@a.example.com")
    with TestingSession() as db:
        agency = db.scalar(select(Agency).where(Agency.slug == "agencia-a"))
        db.add(Client(agency_id=agency.id, name="Cliente Uno", portal_slug="cliente-uno"))
        db.commit()
    infra = client.get(f"/api/platform/agencies/{a['agency']['id']}/infrastructure")
    assert infra.status_code == 200, infra.text
    items = infra.json()
    assert [item["client_name"] for item in items] == ["Cliente Uno"]
    assert "encrypted_dsn" not in json.dumps(items)
    assert client.get("/api/platform/agencies/00000000-0000-0000-0000-000000000000/infrastructure").status_code == 404


@pytest.mark.central_only("seeds a data store and a storage row directly")
def test_infrastructure_reports_health_without_secrets(client):
    _admin(client)
    a = _create(client, "Agencia A", "agencia-a", "a@a.example.com")
    with TestingSession() as db:
        agency = db.scalar(select(Agency).where(Agency.slug == "agencia-a"))
        customer = Client(agency_id=agency.id, name="Cliente", portal_slug="cliente-a")
        db.add(customer)
        db.flush()
        db.add(ClientDataStore(
            agency_id=agency.id, client_id=customer.id, status="error", project_name="proj",
            project_ref="abc123", region="eu-central-1", last_error="boom",
            encrypted_dsn=encrypt_secret("postgresql://user:secret@host/db"),
            connect_expires_at=datetime.now(timezone.utc),
        ))
        db.add(ClientStorageConnection(
            agency_id=agency.id, client_id=customer.id, status="connected", bucket="bucket-one",
        ))
        db.commit()
    infra = client.get(f"/api/platform/agencies/{a['agency']['id']}/infrastructure")
    assert infra.status_code == 200, infra.text
    item = infra.json()[0]
    assert item["datastore"]["status"] == "error"
    assert item["datastore"]["last_error"] == "boom"
    assert item["storage"]["status"] == "connected"
    assert item["storage"]["bucket"] == "bucket-one"
    dumped = json.dumps(infra.json())
    assert "postgresql://" not in dumped
    assert "encrypted" not in dumped
