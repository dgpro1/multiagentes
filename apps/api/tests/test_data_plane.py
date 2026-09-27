"""The data plane of a client, in its own database (phase B, first slice).

A throwaway schema in the test database stands in for the client's Supabase
project: the tenant engine pins its search_path per transaction exactly as it
does against the pooler.
"""

import os
import uuid

import pytest
from sqlalchemy import inspect, select, text

from app import database
from app.data_plane import CONTROL_PLANE, DATA_PLANE, tenant_metadata
from app.database import Base
from app.models import Client, ClientDataStore, Conversation, Message
from app.security import encrypt_secret
from app.services import data_store, tenant_schema
from app.services.tenant_copy import copy_client
from conftest import TestingSession, customer_conversation

URL = os.environ["DATABASE_URL"]


@pytest.fixture
def tenant():
    schema = f"hunterai_t_{uuid.uuid4().hex[:8]}"
    with database.engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = database.tenant_engine(URL, schema)
    try:
        yield schema, engine
    finally:
        engine.dispose()
        with database.engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


def test_every_table_is_on_one_plane():
    names = {table.name for table in Base.metadata.sorted_tables}
    assert not (names - DATA_PLANE - CONTROL_PLANE), "Classify new tables in app/data_plane.py"
    assert not (DATA_PLANE & CONTROL_PLANE)
    assert not ((DATA_PLANE | CONTROL_PLANE) - names), "app/data_plane.py names a table that no longer exists"


def test_the_tenant_revisions_build_what_the_models_describe(tenant):
    """A data-plane model changed without a new migrations_tenant/ file fails here."""
    schema, engine = tenant
    assert tenant_schema.upgrade_engine(engine) == [r for r, _ in tenant_schema.revisions()]
    assert tenant_schema.upgrade_engine(engine) == []  # idempotent

    inspector = inspect(database.engine)
    expected = tenant_metadata()
    built = set(inspector.get_table_names(schema=schema)) - {tenant_schema.VERSION_TABLE}
    assert built == set(expected.tables)
    for table in expected.tables.values():
        columns = {c["name"]: c for c in inspector.get_columns(table.name, schema=schema)}
        assert set(columns) == set(table.c.keys()), f"{table.name}: columns differ from the models; add a tenant revision"
        for column in table.columns:
            assert columns[column.name]["nullable"] == column.nullable, f"{table.name}.{column.name} nullability"
        indexes = {i["name"] for i in inspector.get_indexes(table.name, schema=schema)}
        assert {i.name for i in table.indexes} <= indexes, f"{table.name}: indexes differ; add a tenant revision"
        # Nothing in a client's database points at the control plane.
        for fk in inspector.get_foreign_keys(table.name, schema=schema):
            assert fk["referred_table"] in DATA_PLANE


def _seed(client):
    customer = client.post("/api/clients", json={"name": "Acme", "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post("/api/agents", json={
        "client_id": customer["id"], "provider": "openrouter", "model": "openai/gpt-5.6-luna",
        "name": "Vera", "instructions": "", "personality": "", "is_active": True}).json()
    conversation = customer_conversation(client, agent["id"])
    client.post(f"/api/clients/{customer['id']}/resources", json={"kind": "link", "name": "Book", "url": "https://x.test"})
    with TestingSession() as db:
        first = Message(conversation_id=uuid.UUID(conversation["id"]), role="user", content="hola", sender_type="visitor")
        db.add(first)
        db.flush()
        # A quote pointing at a row of the same table: filled in a second pass.
        db.add(Message(conversation_id=first.conversation_id, role="assistant", content="hola!",
                       sender_type="ai", quoted_message_id=first.id))
        db.commit()
    return customer, conversation


@pytest.mark.central_only("starts from a central client and copies it")
def test_a_client_is_copied_whole_and_verified(authenticated_client, tenant):
    schema, engine = tenant
    tenant_schema.upgrade_engine(engine)
    customer, conversation = _seed(authenticated_client)

    with TestingSession() as db:
        client = db.get(Client, uuid.UUID(customer["id"]))
        result = copy_client(db, client, engine)
    assert result.counts["conversations"] >= 1 and result.counts["messages"] >= 2
    assert result.counts["client_resources"] == 1

    with engine.begin() as conn:
        quoted = conn.execute(text("SELECT count(*) FROM messages WHERE quoted_message_id IS NOT NULL")).scalar()
        assert quoted == 1
        assert conn.execute(text("SELECT count(*) FROM conversations WHERE id = :id"), {"id": conversation["id"]}).scalar() == 1

    # A second copy into the same database is refused rather than merged.
    from fastapi import HTTPException
    with TestingSession() as db, pytest.raises(HTTPException) as refused:
        copy_client(db, db.get(Client, uuid.UUID(customer["id"])), engine)
    assert refused.value.status_code == 409


@pytest.mark.central_only("starts from a central client and copies it")
def test_a_supabase_client_session_reads_its_own_database(authenticated_client, tenant, monkeypatch):
    schema, engine = tenant
    monkeypatch.setattr(data_store, "SCHEMA", schema)
    tenant_schema.upgrade_engine(engine)
    customer, conversation = _seed(authenticated_client)
    with TestingSession() as db:
        client = db.get(Client, uuid.UUID(customer["id"]))
        copy_client(db, client, engine)
        db.add(ClientDataStore(agency_id=client.agency_id, client_id=client.id, status="connected",
                               encrypted_dsn=encrypt_secret(URL), schema_version=tenant_schema.head(),
                               connect_expires_at=client.created_at))
        client.data_mode = "supabase"
        db.commit()
        # Make the two copies distinguishable: rename centrally only.
        db.execute(text("UPDATE conversations SET title = 'central copy' WHERE id = :id"), {"id": conversation["id"]})
        db.commit()

    with TestingSession() as db:
        client = db.get(Client, uuid.UUID(customer["id"]))
        database.use_client(db, client)
        row = db.scalar(select(Conversation).where(Conversation.id == uuid.UUID(conversation["id"])))
        assert row is not None and row.title != "central copy"  # read from the client's database
        assert db.get(Client, client.id).name == "Acme"         # control plane stays central
        # A query mixing both databases is refused with a clear error instead of failing obscurely.
        from app.models import Agent
        with pytest.raises(database.CrossPlaneQuery):
            db.execute(select(Conversation).join(Agent, Agent.id == Conversation.agent_id))
