"""The agency's own Supabase project: connecting it, and moving clients into it.

The test database stands in for the agency's Supabase project: its schemas are
the clients' schemas and real database roles are created for them, so the
isolation tests exercise what Postgres actually enforces. Supabase's own HTTP
API is replaced by functions that run the SQL it would run, locally.
"""

import os
import re
import uuid
from datetime import timedelta
from urllib.parse import urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app import agency_features, database
from app.models import Agency, AgencyDataStore, Client, ClientAgencySchema, ClientDataStore, now_utc
from app.security import encrypt_secret
from app.services import agency_backend, data_store, supabase_mgmt as supabase, tenant_schema, tenant_switch
from conftest import TestingSession

# Clients here start central on purpose; under HUNTERAI_TENANT_TESTS they are born switched.
pytestmark = pytest.mark.central_only("moves clients between databases itself")

URL = os.environ["DATABASE_URL"]
REF = "a" * 20
PROJECT = {"ref": REF, "name": "Agency project", "region": "eu-central-1", "status": "ACTIVE"}


def _dsn(role: str, password: str) -> str:
    parsed = urlparse(URL.replace("postgresql+psycopg://", "postgresql://", 1))
    return f"postgresql://{role}:{password}@{parsed.hostname}:{parsed.port or 5432}{parsed.path}"


def _drop_agency_roles() -> None:
    for engine in list(database._tenant_engines.values()):
        engine.dispose()
    database._tenant_engines.clear()
    with database.engine.begin() as conn:
        for (name,) in conn.execute(text("SELECT rolname FROM pg_roles WHERE rolname LIKE 'hunterai_c_%'")).all():
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
            conn.exec_driver_sql(f'DROP OWNED BY "{name}"')
            conn.exec_driver_sql(f'DROP ROLE "{name}"')


@pytest.fixture
def agency_project(monkeypatch):
    """A Supabase project that is the test database, with the platform's switch on."""
    _drop_agency_roles()
    monkeypatch.setattr(tenant_switch, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(supabase, "configured", lambda: True)
    monkeypatch.setattr(supabase, "authorization_url", lambda state, verifier: f"https://supabase.test/authorize?state={state}")

    async def exchange_code(code, verifier):
        return supabase.Grant("access", "refresh", now_utc() + timedelta(hours=1))

    async def list_projects(access):
        return [PROJECT]

    async def run_query(access, ref, query):
        assert ref == REF
        with database.engine.begin() as conn:
            conn.exec_driver_sql(query)

    async def pooler_config(access, ref):
        return {"db_host": "aws-0.pooler.supabase.com", "db_port": 6543, "db_name": "postgres"}

    monkeypatch.setattr(supabase, "exchange_code", exchange_code)
    monkeypatch.setattr(supabase, "list_projects", list_projects)
    monkeypatch.setattr(supabase, "run_query", run_query)
    monkeypatch.setattr(supabase, "pooler_config", pooler_config)
    monkeypatch.setattr(data_store, "build_dsn", lambda ref, pooler, password, role=data_store.ROLE: _dsn(role, password))
    try:
        yield
    finally:
        _drop_agency_roles()


def _agency_id(client: TestClient) -> uuid.UUID:
    return uuid.UUID(client.get("/api/agency").json()["id"])


def _module(client: TestClient, on: bool) -> None:
    with TestingSession() as db:
        agency = db.get(Agency, _agency_id(client))
        agency.features = agency_features.merged(agency.features, {"agency_backend": on})
        db.commit()


def _connect_project(client: TestClient) -> dict:
    started = client.post("/api/agency/backend/connect")
    assert started.status_code == 200, started.text
    state = re.search(r"state=([^&]+)", started.json()["authorization_url"]).group(1)
    back = client.get("/api/supabase/oauth/callback", params={"state": state, "code": "granted"}, follow_redirects=False)
    assert back.status_code == 307 and back.headers["location"].endswith("/settings?backend=authorized"), back.headers
    assert client.get("/api/agency/backend/projects").json() == [PROJECT]
    chosen = client.post("/api/agency/backend/project", json={"ref": REF})
    assert chosen.status_code == 200, chosen.text
    return chosen.json()


def _widget_client(client: TestClient, name: str = "Acme") -> tuple[dict, str]:
    customer = client.post("/api/clients", json={"name": name, "is_active": True}).json()
    agent = client.post("/api/agents", json={
        "client_id": customer["id"], "name": "Vera", "instructions": "", "personality": "", "model": "", "is_active": True,
    }).json()
    channel = client.put(f"/api/webchat/channels/{customer['id']}", json={
        "agent_id": agent["id"], "greeting": "Hola", "color": "#075985", "is_enabled": True,
    }).json()
    public_id = channel["public_id"]
    assert client.post(f"/api/widget/{public_id}/messages", json={"session_id": "s1", "content": "hola"}).status_code == 200
    return customer, public_id


def _schema_of(customer: dict) -> ClientAgencySchema:
    with TestingSession() as db:
        return db.scalar(select(ClientAgencySchema).where(ClientAgencySchema.client_id == uuid.UUID(customer["id"])))


def _count_in_schema(schema: str, sql: str, **params):
    with database.engine.connect() as conn:
        conn.exec_driver_sql(f'SET search_path TO "{schema}"')
        return conn.execute(text(sql), params).scalar()


def _central(sql: str, **params):
    with database.engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def _switch(client: TestClient, customer: dict, target: str):
    return client.post(f"/api/clients/{customer['id']}/datastore/switch", json={"target": target})


# --- the platform's switch ----------------------------------------------------------------


def test_the_module_ships_off_and_gates_every_entry(authenticated_client, agency_project):
    client = authenticated_client
    assert agency_features.DEFAULTS["agency_backend"] is False
    state = client.get("/api/agency/backend").json()
    assert (state["status"], state["module_enabled"]) == ("none", False)
    assert client.post("/api/agency/backend/connect").status_code == 403
    assert client.get("/api/agency/backend/projects").status_code == 403
    customer, _ = _widget_client(client)
    assert _switch(client, customer, "agency").status_code == 403
    # The panel's own picture of the client says why the option is not offered.
    assert client.get(f"/api/clients/{customer['id']}/datastore").json()["agency_backend_ready"] is False


def test_the_pro_preset_does_not_switch_it_on():
    assert "agency_backend" not in agency_features.PRESETS["pro"]
    assert "agency_backend" in agency_features.PRESETS["full"]


# --- connecting the project ---------------------------------------------------------------


def test_an_agency_connects_its_project_once_and_can_disconnect_it(authenticated_client, agency_project):
    client = authenticated_client
    _module(client, True)
    connected = _connect_project(client)
    assert (connected["status"], connected["project_name"], connected["region"]) == ("connected", "Agency project", "eu-central-1")
    assert client.post("/api/agency/backend/check").json()["status"] == "connected"
    # Nothing secret comes back.
    assert "token" not in str(connected).lower() and "dsn" not in str(connected).lower()

    gone = client.delete("/api/agency/backend")
    assert gone.status_code == 200 and gone.json()["status"] == "pending" and gone.json()["project_ref"] == ""
    with TestingSession() as db:
        store = db.scalar(select(AgencyDataStore))
        assert store.encrypted_refresh_token is None and store.encrypted_access_token is None


def test_a_refused_or_stale_authorization_connects_nothing(authenticated_client, agency_project):
    client = authenticated_client
    _module(client, True)
    state = re.search(r"state=([^&]+)", client.post("/api/agency/backend/connect").json()["authorization_url"]).group(1)
    denied = client.get("/api/supabase/oauth/callback", params={"state": state, "error": "access_denied"}, follow_redirects=False)
    assert denied.headers["location"].endswith("/settings?backend=denied")
    # An authorization is single use, and a made-up state is not one.
    again = client.get("/api/supabase/oauth/callback", params={"state": state, "code": "x"}, follow_redirects=False)
    assert "expired" in again.headers["location"]
    assert client.get("/api/agency/backend").json()["status"] == "authorized" or client.get("/api/agency/backend").json()["status"] == "pending"
    assert client.post("/api/agency/backend/project", json={"ref": "b" * 20}).status_code in (404, 409)
    assert client.post("/api/agency/backend/project", json={"ref": "short"}).status_code == 422


# --- moving a client in and out -----------------------------------------------------------


def test_a_client_moves_into_the_agency_project_and_keeps_working(authenticated_client, agency_project):
    client = authenticated_client
    _module(client, True)
    _connect_project(client)
    customer, public_id = _widget_client(client)
    assert client.get(f"/api/clients/{customer['id']}/datastore").json()["agency_backend_ready"] is True

    moved = _switch(client, customer, "agency")
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["data_mode"] == "agency" and body["counts"]["messages"] >= 1
    assert (body["agency_schema_status"], body["agency_schema_version"]) == ("connected", tenant_schema.head())
    assert body["agency_schema_retired_at"] is None

    schema = _schema_of(customer)
    assert schema.schema_name == schema.role_name == agency_backend.schema_name_for(uuid.UUID(customer["id"]))
    before = _count_in_schema(schema.schema_name, "SELECT count(*) FROM messages")
    assert client.post(f"/api/widget/{public_id}/messages", json={"session_id": "s1", "content": "otra"}).status_code == 200
    # The new message went to the client's schema, not the central database.
    assert _count_in_schema(schema.schema_name, "SELECT count(*) FROM messages") > before
    assert _central("SELECT count(*) FROM messages WHERE content = 'otra'") == 0
    assert any(c["client_id"] == customer["id"] for c in client.get("/api/conversations").json())
    # The project cannot be disconnected or swapped while clients live in it.
    assert client.delete("/api/agency/backend").status_code == 409
    assert client.post("/api/agency/backend/project", json={"ref": "b" * 20}).status_code == 409
    assert client.get("/api/agency/backend").json()["clients_in_agency"] == 1


def test_a_client_cannot_read_another_clients_schema(authenticated_client, agency_project):
    client = authenticated_client
    _module(client, True)
    _connect_project(client)
    first, _ = _widget_client(client, "First")
    second, _ = _widget_client(client, "Second")
    assert _switch(client, first, "agency").status_code == 200
    assert _switch(client, second, "agency").status_code == 200
    a, b = _schema_of(first), _schema_of(second)
    assert a.schema_name != b.schema_name

    engine_a = database.tenant_engine(data_store.decrypt_secret(a.encrypted_dsn), a.schema_name)
    with engine_a.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM messages")).scalar() >= 1
    # Its own role is refused on the other schema, by Postgres, whatever the application does.
    with pytest.raises(DBAPIError) as refused:
        with engine_a.connect() as conn:
            conn.execute(text(f'SELECT count(*) FROM "{b.schema_name}".messages'))
    assert isinstance(refused.value.orig, psycopg.errors.InsufficientPrivilege)
    # Nor can it create anything there.
    with pytest.raises(DBAPIError) as refused:
        with engine_a.begin() as conn:
            conn.execute(text(f'CREATE TABLE "{b.schema_name}".intruder (id int)'))
    assert isinstance(refused.value.orig, psycopg.errors.InsufficientPrivilege)


def test_moving_back_returns_the_data_and_keeps_a_retired_copy(authenticated_client, agency_project):
    client = authenticated_client
    _module(client, True)
    _connect_project(client)
    customer, _ = _widget_client(client)
    moved = _switch(client, customer, "agency").json()
    back = _switch(client, customer, "central")
    assert back.status_code == 200, back.text
    assert back.json()["data_mode"] == "central" and back.json()["counts"] == moved["counts"]
    assert back.json()["agency_schema_retired_at"] is not None
    schema = _schema_of(customer)
    # The rows stay in the agency's schema as a safety copy; the central ones are the live ones again.
    assert _count_in_schema(schema.schema_name, "SELECT count(*) FROM messages") >= 1
    assert _central("SELECT count(*) FROM messages") >= 1

    again = _switch(client, customer, "agency")
    assert again.status_code == 200 and again.json()["agency_schema_retired_at"] is None
    assert _switch(client, customer, "agency").status_code == 409  # already there


def test_the_agency_and_the_clients_own_project_go_through_the_central_database(authenticated_client, agency_project):
    client = authenticated_client
    _module(client, True)
    _connect_project(client)
    customer, _ = _widget_client(client)
    own = f"hunterai_s_{uuid.uuid4().hex[:8]}"
    with database.engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{own}"')
    engine = database.tenant_engine(URL, own)
    tenant_schema.upgrade_engine(engine)
    original = data_store.SCHEMA
    data_store.SCHEMA = own
    try:
        with TestingSession() as db:
            c = db.get(Client, uuid.UUID(customer["id"]))
            db.add(ClientDataStore(
                agency_id=c.agency_id, client_id=c.id, status="connected", encrypted_dsn=encrypt_secret(URL),
                schema_version=tenant_schema.head(), connect_expires_at=now_utc() + timedelta(days=1),
            ))
            db.commit()
        assert _switch(client, customer, "agency").json()["data_mode"] == "agency"
        # Agency -> the client's own project: two hops, one request.
        own_move = _switch(client, customer, "supabase")
        assert own_move.status_code == 200, own_move.text
        assert own_move.json()["data_mode"] == "supabase"
        assert _count_in_schema(own, "SELECT count(*) FROM messages") >= 1
        assert _central("SELECT count(*) FROM messages") == 0
        assert own_move.json()["agency_schema_retired_at"] is not None
        # ...and from there straight back to the agency.
        assert _switch(client, customer, "agency").json()["data_mode"] == "agency"
    finally:
        data_store.SCHEMA = original
        engine.dispose()
        with database.engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{own}" CASCADE')


def test_switching_the_module_off_keeps_clients_working_and_refuses_new_moves(authenticated_client, agency_project):
    client = authenticated_client
    _module(client, True)
    _connect_project(client)
    inside, public_id = _widget_client(client, "Inside")
    outside, _ = _widget_client(client, "Outside")
    assert _switch(client, inside, "agency").status_code == 200

    _module(client, False)
    # It still answers and still takes messages...
    assert client.post(f"/api/widget/{public_id}/messages", json={"session_id": "s2", "content": "sigue"}).status_code == 200
    assert client.get(f"/api/clients/{inside['id']}/datastore").json()["data_mode"] == "agency"
    # ...nobody new can enter...
    assert _switch(client, outside, "agency").status_code == 403
    # ...and leaving is always allowed.
    assert _switch(client, inside, "central").status_code == 200


def test_a_failed_move_into_the_agency_leaves_the_client_central(authenticated_client, agency_project, monkeypatch):
    client = authenticated_client
    _module(client, True)
    _connect_project(client)
    customer, public_id = _widget_client(client)

    def broken(*args, **kwargs):
        raise RuntimeError("the network dropped")

    monkeypatch.setattr(tenant_switch, "copy_client", broken)
    with pytest.raises(RuntimeError):
        _switch(client, customer, "agency")
    after = client.get(f"/api/clients/{customer['id']}/datastore").json()
    assert after["data_mode"] == "central"
    assert client.post(f"/api/widget/{public_id}/messages", json={"session_id": "s1", "content": "sigue"}).status_code == 200


# --- the client's schema ------------------------------------------------------------------


def test_provisioning_keeps_a_working_role_and_replaces_a_dead_one(authenticated_client, agency_project):
    client = authenticated_client
    _module(client, True)
    _connect_project(client)
    customer, _ = _widget_client(client)

    async def provision():
        with TestingSession() as db:
            row = await agency_backend.provision_client(db, db.get(Client, uuid.UUID(customer["id"])))
            return row.encrypted_dsn

    import asyncio

    first = asyncio.run(provision())
    assert asyncio.run(provision()) == first  # a working login is left alone
    dead = encrypt_secret(_dsn("hunterai_c_" + "0" * 20, "nope"))
    with TestingSession() as db:
        db.execute(update(ClientAgencySchema).values(encrypted_dsn=dead))
        db.commit()
    replaced = asyncio.run(provision())
    assert replaced not in (first, dead)


def test_the_boot_time_update_brings_agency_schemas_forward(authenticated_client, agency_project):
    client = authenticated_client
    _module(client, True)
    _connect_project(client)
    customer, _ = _widget_client(client)
    assert _switch(client, customer, "agency").status_code == 200
    with TestingSession() as db:
        db.execute(update(ClientAgencySchema).values(schema_version="t0000"))
        db.commit()
    with TestingSession() as db:
        updated = tenant_schema.upgrade_all(db)
    assert uuid.UUID(customer["id"]) in updated
    assert client.get(f"/api/clients/{customer['id']}/datastore").json()["agency_schema_version"] == tenant_schema.head()


def test_names_and_sql_are_built_only_from_ids_and_hex():
    name = agency_backend.schema_name_for(uuid.UUID("12345678-1234-5678-1234-567812345678"))
    assert name == "hunterai_c_12345678123456781234"
    sql = agency_backend.provisioning_sql(name, "0" * 48)
    assert f"REVOKE ALL ON SCHEMA {name} FROM PUBLIC" in sql and f"GRANT USAGE, CREATE ON SCHEMA {name} TO {name}" in sql
    for bad_name, bad_password in (("x; DROP TABLE users", "0" * 48), (name, "'; --"), (name.upper(), "0" * 48)):
        with pytest.raises(AssertionError):
            agency_backend.provisioning_sql(bad_name, bad_password)


def test_the_routes_need_a_signed_in_person(authenticated_client, agency_project):
    anonymous = TestClient(authenticated_client.app)
    for method, path in (("get", "/api/agency/backend"), ("post", "/api/agency/backend/connect"), ("delete", "/api/agency/backend")):
        assert getattr(anonymous, method)(path).status_code == 401, (method, path)


def test_the_platform_moves_a_clients_data_and_audits_it(authenticated_client, agency_project):
    from test_platform_auth import _login, _platform_admin

    client = authenticated_client
    _module(client, True)
    _connect_project(client)
    customer, _ = _widget_client(client)
    platform = TestClient(client.app)
    _platform_admin()
    _login(platform)
    base = f"/api/platform/agencies/{_agency_id(client)}/clients"

    moved = platform.post(f"{base}/{customer['id']}/datastore/switch", json={"target": "agency"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["data_mode"] == "agency" and moved.json()["counts"]["messages"] >= 1
    assert platform.post(f"{base}/{customer['id']}/datastore/switch", json={"target": "central"}).json()["data_mode"] == "central"
    events = [e for e in platform.get("/api/platform/audit-events").json() if e["action"] == "client.data_moved"]
    assert sorted((e["details"]["from"], e["details"]["to"]) for e in events) == [("agency", "central"), ("central", "agency")]

    assert platform.post(f"{base}/{uuid.uuid4()}/datastore/switch", json={"target": "central"}).status_code == 404
    assert platform.post(f"{base}/{customer['id']}/datastore/switch", json={"target": "nowhere"}).status_code == 422
    _module(client, False)
    assert platform.post(f"{base}/{customer['id']}/datastore/switch", json={"target": "agency"}).status_code == 403
