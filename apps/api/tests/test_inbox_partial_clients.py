"""A client whose data is being moved must not empty the list that spans the
agency.

The agency inbox merges every client's leads out of every database the agency
uses. One client whose own database is a step behind used to take the whole
request with it: the merge raised before returning anything, and the leads of
the clients that were perfectly readable never arrived. This is what that looks
like when it happens, and what it does instead.

A caller that named one client is a different case and keeps failing loudly:
"no conversations" and "could not read them" are not the same answer.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import TestingSession, customer_conversation


def _central_lead(client: TestClient) -> dict:
    """One client on the central database, with a lead on it."""
    customer = client.post("/api/clients", json={"name": "Central Co", "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post(
        "/api/agents",
        json={"client_id": customer["id"], "provider": "openrouter", "model": "gpt-4.1-mini",
              "name": "Vera", "instructions": "", "personality": "", "is_active": True},
    ).json()
    return customer_conversation(client, agent["id"])


def _client_whose_data_is_moving(client: TestClient, name: str = "Moving Co") -> dict:
    """A client in the state a half-migrated one is really in: it has its own
    database, so the sweep goes to it, and that database is not the one this
    release expects, so ``use_client`` refuses it."""
    from app.models import Client
    from app.services import tenant_schema
    from app.security import encrypt_secret

    created = client.post("/api/clients", json={"name": name, "is_active": True}).json()
    with TestingSession() as db:
        row = db.get(Client, uuid.UUID(created["id"]))
        row.data_mode = "supabase"
        store = row.data_store
        if store is None:
            from app.models import ClientDataStore

            store = ClientDataStore(agency_id=row.agency_id, client_id=row.id)
            db.add(store)
        store.status = "connected"
        store.provider = "supabase"
        # Reachable, and one revision behind: the version check is what refuses it.
        store.encrypted_dsn = encrypt_secret("postgresql+psycopg://openlivery:openlivery@localhost:5432/openlivery_test")
        store.schema_version = "t0000"
        db.commit()
    assert tenant_schema.head() != "t0000"
    return created


def test_the_leads_of_readable_clients_survive_a_client_whose_data_is_moving(authenticated_client: TestClient):
    client = authenticated_client
    lead = _central_lead(client)
    _client_whose_data_is_moving(client)

    listed = client.get("/api/conversations/inbox")
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()] == [lead["id"]], "the readable client's lead must be there"


def test_the_answer_says_which_client_is_missing(authenticated_client: TestClient):
    """A short list for the whole agency is a different thing from a short list
    for one client, and the panel can only say so if the server tells it."""
    client = authenticated_client
    _central_lead(client)
    _client_whose_data_is_moving(client)

    listed = client.get("/api/conversations/inbox")
    assert listed.status_code == 200
    assert listed.headers.get("X-Incomplete-Clients") == "Moving Co"


def test_nothing_is_reported_when_every_client_can_be_read(authenticated_client: TestClient):
    client = authenticated_client
    lead = _central_lead(client)

    listed = client.get("/api/conversations/inbox")
    assert listed.status_code == 200
    assert "X-Incomplete-Clients" not in listed.headers
    assert [row["id"] for row in listed.json()] == [lead["id"]]


def test_asking_about_one_client_still_fails_loudly(authenticated_client: TestClient):
    """The list above is the one that carries on. A caller that named the client
    that is moving has nothing to carry on to, and must not be told that client
    simply has no conversations."""
    client = authenticated_client
    lead = _central_lead(client)
    moving = _client_whose_data_is_moving(client)
    central_id = next(row["id"] for row in client.get("/api/clients").json() if row["name"] == "Central Co")

    still_answers = client.get(f"/api/conversations?client_id={central_id}")
    assert still_answers.status_code == 200, still_answers.text
    assert [row["id"] for row in still_answers.json()] == [lead["id"]]

    refused = client.get(f"/api/conversations?client_id={moving['id']}")
    assert refused.status_code == 503, refused.text


def test_the_dashboard_survives_it_too(authenticated_client: TestClient):
    """The inbox was not the only list built out of several databases; the
    dashboard reads the same way."""
    client = authenticated_client
    _central_lead(client)
    _client_whose_data_is_moving(client)

    listed = client.get("/api/dashboard")
    assert listed.status_code == 200, listed.text[:200]
