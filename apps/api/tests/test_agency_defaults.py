"""A new client starts in its agency's own Supabase project and R2 bucket, and an
agency can connect Supabase with a personal access token.

The fakes are the ones of the two agency-backend suites: the test database is
the agency's project, and boto3 is replaced by one in-memory bucket per key.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Client
from app.services import supabase_mgmt as supabase
from conftest import TestingSession
from test_agency_backend import REF, _agency_id, _connect_project, _module, agency_project  # noqa: F401
from test_agency_storage import AGENCY, FakeS3, buckets  # noqa: F401

TOKEN = "sbp_" + "a1b2c3d4" * 6


def _create(client: TestClient, name: str = "Acme") -> dict:
    created = client.post("/api/clients", json={"name": name, "is_active": True})
    assert created.status_code == 201, created.text
    return created.json()


def _state(client: TestClient, customer: dict) -> tuple[dict, dict]:
    base = f"/api/clients/{customer['id']}"
    return client.get(f"{base}/datastore").json(), client.get(f"{base}/storage").json()


def _connect_bucket(client: TestClient) -> None:
    assert client.put("/api/agency/storage", json=AGENCY).status_code == 200


# --- the default -------------------------------------------------------------------------------


def test_a_new_client_starts_in_the_agencys_project_and_bucket(authenticated_client, agency_project, buckets):
    client = authenticated_client
    _module(client, True)
    _connect_project(client)
    _connect_bucket(client)
    data, files = _state(client, _create(client))
    assert data["data_mode"] == "agency" and data["agency_schema_status"] == "connected"
    assert files["hosted_by"] == "agency" and files["status"] == "connected"
    # And it works from the first request: an agent can be created and listed.
    customer = _create(client, "Second")
    agent = client.post("/api/agents", json={
        "client_id": customer["id"], "name": "Vera", "instructions": "", "personality": "", "model": "", "is_active": True,
    })
    assert agent.status_code in (200, 201), agent.text


def test_a_client_made_through_the_v1_api_starts_there_too(authenticated_client, agency_project, buckets):
    client = authenticated_client
    _module(client, True)
    _connect_project(client)
    created = client.post("/api/v1/clients", json={"name": "Via API", "is_active": True})
    assert created.status_code == 201, created.text
    data, _ = _state(client, created.json())
    assert data["data_mode"] == "agency"


def test_only_what_the_agency_has_ready_is_used(authenticated_client, agency_project, buckets):
    client = authenticated_client
    _module(client, True)
    _connect_bucket(client)  # a bucket, but no Supabase project
    data, files = _state(client, _create(client, "Bucket only"))
    assert data["data_mode"] == "central" and files["hosted_by"] == "agency"

    _connect_project(client)
    data, files = _state(client, _create(client, "Both"))
    assert data["data_mode"] == "agency" and files["hosted_by"] == "agency"


def test_without_the_module_or_a_connection_a_client_starts_as_always(authenticated_client, agency_project, buckets):
    client = authenticated_client
    data, files = _state(client, _create(client, "No module"))
    assert data["data_mode"] == "central" and files["hosted_by"] == "client"
    _module(client, True)
    data, _ = _state(client, _create(client, "Nothing connected"))
    assert data["data_mode"] == "central"
    # Clients that existed before the agency connected anything are not touched.
    _connect_project(client)
    assert client.get(f"/api/clients/{_create(client, 'Later')['id']}/datastore").json()["data_mode"] == "agency"
    with TestingSession() as db:
        modes = {c.name: c.data_mode for c in db.scalars(select(Client))}
    assert modes["No module"] == "central" and modes["Nothing connected"] == "central"


def test_a_backend_that_fails_never_fails_the_creation(authenticated_client, agency_project, buckets, monkeypatch):
    client = authenticated_client
    _module(client, True)
    _connect_project(client)

    async def down(access, ref, query):
        raise supabase.SupabaseError("Supabase is down")

    monkeypatch.setattr(supabase, "run_query", down)
    customer = _create(client, "Survives")
    data, _ = _state(client, customer)
    assert data["data_mode"] == "central"
    # The reason is there for the panel, and the client still works.
    assert data["agency_schema_status"] == "error" and "down" in (data["agency_schema_last_error"] or "")
    assert client.get(f"/api/clients/{customer['id']}").status_code == 200


# --- connecting Supabase with a token --------------------------------------------------------------


def test_an_agency_connects_supabase_with_a_personal_access_token(authenticated_client, agency_project):
    client = authenticated_client
    assert client.put("/api/agency/backend/token", json={"token": TOKEN}).status_code == 403  # the module ships off
    _module(client, True)
    ok = client.put("/api/agency/backend/token", json={"token": TOKEN})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "authorized" and TOKEN not in ok.text
    chosen = client.post("/api/agency/backend/project", json={"ref": REF})
    assert chosen.status_code == 200 and chosen.json()["status"] == "connected"
    # The token is what every later call runs with: a client can move in.
    customer = _create(client)
    assert client.post(f"/api/clients/{customer['id']}/datastore/switch", json={"target": "agency"}).status_code in (200, 409)
    assert client.get(f"/api/clients/{customer['id']}/datastore").json()["data_mode"] == "agency"
    assert client.post("/api/agency/backend/check").json()["status"] == "connected"
    assert client.delete("/api/agency/backend").status_code == 409  # a client lives there
    with TestingSession() as db:
        from app.models import AgencyDataStore

        store = db.scalar(select(AgencyDataStore))
        assert store.encrypted_refresh_token is None and store.access_token_expires_at is None
        assert TOKEN not in (store.encrypted_access_token or "")  # stored encrypted


def test_a_token_supabase_refuses_is_not_kept(authenticated_client, agency_project, monkeypatch):
    client = authenticated_client
    _module(client, True)

    async def refuse(access):
        raise supabase.SupabaseError("refused")

    monkeypatch.setattr(supabase, "list_projects", refuse)
    refused = client.put("/api/agency/backend/token", json={"token": TOKEN})
    assert refused.status_code == 422
    assert client.get("/api/agency/backend").json()["status"] in ("none", "pending")
    for bad in ("too short", "has spaces " * 4):
        assert client.put("/api/agency/backend/token", json={"token": bad}).status_code in (422,)
    assert uuid.UUID(str(_agency_id(client)))
