"""Moving a client's data to its own database and back (phase B, slice 2).

A throwaway schema of the test database stands in for the client's Supabase
project, as in test_data_plane.py.
"""

import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app import database
from app.models import Client, ClientDataStore, PendingInbound, now_utc
from app.security import encrypt_secret
from app.services import data_store, tenant_schema, tenant_switch
from conftest import TestingSession

# Clients here start central on purpose; under HUNTERAI_TENANT_TESTS they are born switched.
pytestmark = pytest.mark.central_only("moves clients between databases itself")

URL = os.environ["DATABASE_URL"]


@pytest.fixture
def own_database(monkeypatch):
    schema = f"hunterai_s_{uuid.uuid4().hex[:8]}"
    with database.engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = database.tenant_engine(URL, schema)
    tenant_schema.upgrade_engine(engine)
    monkeypatch.setattr(data_store, "SCHEMA", schema)
    monkeypatch.setattr(tenant_switch, "SETTLE_SECONDS", 0)
    try:
        yield engine
    finally:
        engine.dispose()
        with database.engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


def _widget_client(client):
    customer = client.post("/api/clients", json={"name": "Acme", "is_active": True}).json()
    agent = client.post("/api/agents", json={
        "client_id": customer["id"], "name": "Vera", "instructions": "", "personality": "", "model": "", "is_active": True,
    }).json()
    channel = client.put(f"/api/webchat/channels/{customer['id']}", json={
        "agent_id": agent["id"], "greeting": "Hola", "color": "#075985", "is_enabled": True,
    }).json()
    public_id = channel["public_id"]
    assert client.post(f"/api/widget/{public_id}/messages", json={"session_id": "s1", "content": "hola"}).status_code == 200
    with TestingSession() as db:
        c = db.get(Client, uuid.UUID(customer["id"]))
        db.add(ClientDataStore(agency_id=c.agency_id, client_id=c.id, status="connected", encrypted_dsn=encrypt_secret(URL),
                               schema_version=tenant_schema.head(), connect_expires_at=now_utc() + timedelta(days=1)))
        db.commit()
    return customer, public_id


def _count(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def _central(sql, **params):
    with database.engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def test_a_client_moves_to_its_own_database_and_keeps_working_there(authenticated_client, own_database):
    client = authenticated_client
    customer, public_id = _widget_client(client)
    moved = client.post(f"/api/clients/{customer['id']}/datastore/switch", json={"target": "supabase"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["data_mode"] == "supabase" and moved.json()["counts"]["messages"] >= 1

    before = _count(own_database, "SELECT count(*) FROM messages")
    assert client.post(f"/api/widget/{public_id}/messages", json={"session_id": "s1", "content": "otra"}).status_code == 200
    # The new message went to the client's database, not the central one.
    assert _count(own_database, "SELECT count(*) FROM messages") > before
    assert _central("SELECT count(*) FROM messages WHERE content = 'otra'") == 0
    # Its conversation keeps showing in the agency panel.
    assert any(c["client_id"] == customer["id"] for c in client.get("/api/conversations").json())
    # The database cannot be disconnected while the data lives there.
    assert client.delete(f"/api/clients/{customer['id']}/datastore").status_code == 409


def test_webhooks_kept_during_the_move_are_replayed_after(authenticated_client, own_database):
    client = authenticated_client
    customer, _ = _widget_client(client)
    replayed: list[dict] = []

    async def fake_replay(db, payload):
        replayed.append(payload)

    tenant_switch.register_replayer("test", fake_replay)
    with TestingSession() as db:
        db.add(PendingInbound(client_id=uuid.UUID(customer["id"]), source="test", payload={"n": 1}))
        db.commit()
    assert client.post(f"/api/clients/{customer['id']}/datastore/switch", json={"target": "supabase"}).status_code == 200
    assert replayed == [{"n": 1}]


def test_a_failed_copy_leaves_the_client_where_it_was(authenticated_client, own_database, monkeypatch):
    client = authenticated_client
    customer, public_id = _widget_client(client)

    def broken(*args, **kwargs):
        raise RuntimeError("the network dropped")

    monkeypatch.setattr(tenant_switch, "copy_client", broken)
    with pytest.raises(RuntimeError):
        client.post(f"/api/clients/{customer['id']}/datastore/switch", json={"target": "supabase"})
    assert client.get(f"/api/clients/{customer['id']}/datastore").json()["data_mode"] == "central"
    assert client.post(f"/api/widget/{public_id}/messages", json={"session_id": "s1", "content": "sigo"}).status_code == 200


def test_moving_back_brings_what_was_written_in_the_clients_database(authenticated_client, own_database):
    client = authenticated_client
    customer, public_id = _widget_client(client)
    client.post(f"/api/clients/{customer['id']}/datastore/switch", json={"target": "supabase"})
    client.post(f"/api/widget/{public_id}/messages", json={"session_id": "s1", "content": "escrito alla"})
    back = client.post(f"/api/clients/{customer['id']}/datastore/switch", json={"target": "central"})
    assert back.status_code == 200, back.text and back.json()["data_mode"] == "central"
    assert _central("SELECT count(*) FROM messages WHERE content = 'escrito alla'") == 1


def test_moving_leaves_no_stale_central_copy(authenticated_client, own_database):
    client = authenticated_client
    customer, _ = _widget_client(client)
    client.post(f"/api/clients/{customer['id']}/datastore/switch", json={"target": "supabase"})
    # The verified copy is the only one: nothing is listed twice, and no central
    # sweep can pick up an old conversation.
    assert _central("SELECT count(*) FROM conversations WHERE client_id = :c", c=customer["id"]) == 0
    assert _count(own_database, "SELECT count(*) FROM conversations WHERE client_id = :c", c=customer["id"]) >= 1
    listed = [row["id"] for row in client.get("/api/conversations").json() if row["client_id"] == customer["id"]]
    assert len(listed) == len(set(listed)) >= 1

def test_nothing_reads_or_writes_a_client_while_it_is_switching(authenticated_client, own_database):
    client = authenticated_client
    customer, public_id = _widget_client(client)
    with TestingSession() as db:
        db.get(Client, uuid.UUID(customer["id"])).data_mode = "switching"
        db.commit()
    busy = client.post(f"/api/widget/{public_id}/messages", json={"session_id": "s1", "content": "ahora"})
    assert busy.status_code == 503 and busy.headers.get("retry-after") == "30"
    assert client.get(f"/api/clients/{customer['id']}/services").status_code == 503


def test_deleting_a_client_never_touches_its_own_database(authenticated_client, own_database):
    client = authenticated_client
    customer, _ = _widget_client(client)
    client.post(f"/api/clients/{customer['id']}/datastore/switch", json={"target": "supabase"})
    held = _count(own_database, "SELECT count(*) FROM messages")
    assert client.delete(f"/api/clients/{customer['id']}").status_code == 204
    assert _count(own_database, "SELECT count(*) FROM messages") == held


def test_a_database_behind_the_schema_is_held_until_boot_brings_it_forward(authenticated_client, own_database):
    client = authenticated_client
    customer, public_id = _widget_client(client)
    client.post(f"/api/clients/{customer['id']}/datastore/switch", json={"target": "supabase"})
    with TestingSession() as db:
        db.get(Client, uuid.UUID(customer["id"])).data_store.schema_version = "t0000"
        db.commit()
    # Behind: nothing reads or writes it.
    assert client.post(f"/api/widget/{public_id}/messages", json={"session_id": "s1", "content": "x"}).status_code == 503
    with TestingSession() as db:
        assert tenant_schema.upgrade_all(db) == [uuid.UUID(customer["id"])]
    assert client.post(f"/api/widget/{public_id}/messages", json={"session_id": "s1", "content": "y"}).status_code == 200


@pytest.mark.anyio
async def test_kept_webhooks_are_replayed_once_the_database_answers(authenticated_client, own_database):
    client = authenticated_client
    customer, _ = _widget_client(client)
    client.post(f"/api/clients/{customer['id']}/datastore/switch", json={"target": "supabase"})
    replayed: list[dict] = []

    async def fake_replay(db, payload):
        replayed.append(payload)

    tenant_switch.register_replayer("test", fake_replay)
    with TestingSession() as db:
        db.add(PendingInbound(client_id=uuid.UUID(customer["id"]), source="test", payload={"n": 2}))
        db.commit()
    assert await tenant_switch.retry_kept() == 1
    assert replayed == [{"n": 2}]
    assert await tenant_switch.retry_kept() == 0  # processed once


def test_agency_views_count_clients_in_their_own_database_and_central_ones(authenticated_client, own_database):
    from datetime import datetime, timezone

    client = authenticated_client
    moved, _ = _widget_client(client)
    client.post(f"/api/clients/{moved['id']}/datastore/switch", json={"target": "supabase"})
    central = client.post("/api/clients", json={"name": "Central Co", "is_active": True}).json()
    agent = client.post("/api/agents", json={
        "client_id": central["id"], "name": "Cora", "instructions": "", "personality": "", "model": "", "is_active": True}).json()
    client.post("/api/conversations", json={"agent_id": agent["id"]})

    listed = {row["client_id"] for row in client.get("/api/conversations").json()}
    assert {moved["id"], central["id"]} <= listed
    assert client.get("/api/dashboard").json()["conversations"] >= 2
    # The report window is UTC: the local date can already be yesterday there
    # around midnight, which would empty the report and flake the test.
    today = datetime.now(timezone.utc).date().isoformat()
    ops = client.get(f"/api/reports/operations?from={today}&to={today}").json()
    assert moved["id"] in {row["id"] for row in ops["by_client"]}
    filters = client.get("/api/reports/filters").json()
    assert "widget" in filters["channels"]


def test_deleting_a_team_clears_it_in_the_clients_database(authenticated_client, own_database):
    client = authenticated_client
    customer, public_id = _widget_client(client)
    team = client.post(f"/api/clients/{customer['id']}/teams", json={"name": "Ventas"}).json()
    client.post(f"/api/clients/{customer['id']}/datastore/switch", json={"target": "supabase"})
    with own_database.begin() as conn:
        conn.execute(text("UPDATE conversations SET team_id = :t WHERE client_id = :c"), {"t": team["id"], "c": customer["id"]})
    assert client.delete(f"/api/clients/{customer['id']}/teams/{team['id']}").status_code == 204
    assert _count(own_database, "SELECT count(*) FROM conversations WHERE team_id IS NOT NULL") == 0


def test_moving_there_again_after_coming_back_replaces_the_old_copy(authenticated_client, own_database):
    client = authenticated_client
    customer, public_id = _widget_client(client)
    client.post(f"/api/clients/{customer['id']}/datastore/switch", json={"target": "supabase"})
    client.post(f"/api/clients/{customer['id']}/datastore/switch", json={"target": "central"})
    client.post(f"/api/widget/{public_id}/messages", json={"session_id": "s1", "content": "de vuelta"})
    again = client.post(f"/api/clients/{customer['id']}/datastore/switch", json={"target": "supabase"})
    assert again.status_code == 200, again.text
    assert _count(own_database, "SELECT count(*) FROM messages WHERE content = 'de vuelta'") == 1
    assert _central("SELECT count(*) FROM conversations WHERE client_id = :c", c=customer["id"]) == 0
