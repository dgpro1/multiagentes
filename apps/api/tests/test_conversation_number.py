"""Every conversation carries a short number, unique per client (#1, #2, ...).

The number is handed out by one ``before_insert`` hook on ``Conversation`` that
bumps ``clients.conversation_seq`` with a row lock, so these tests cover the
counting, the creators that never mention it, the by-number routes, the
migration's backfill and the concurrency guarantee.
"""

import importlib.util
import pathlib
import threading
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.config import get_settings
from app.models import Agency, Agent, Client, Conversation, WebhookDelivery
from app.services import ai as ai_service
from app.services import evolution as evolution_driver
from app.services import whatsapp_inbound as whatsapp_inbound_service
from conftest import TestingSession, customer_conversation, test_engine

MIGRATION = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0058_conversation_number.py"


def _seed(db: Session, suffix: str = "a") -> tuple[Client, Agent]:
    agency = Agency(name=f"Agency {suffix}", slug=f"agency-{suffix}")
    db.add(agency)
    db.flush()
    client = Client(agency_id=agency.id, name=f"Client {suffix}", portal_slug=f"client-{suffix}")
    db.add(client)
    db.flush()
    agent = Agent(agency_id=agency.id, client_id=client.id, name="Agent")
    db.add(agent)
    db.flush()
    return client, agent


def _conversation(client: Client, agent: Agent, **fields) -> Conversation:
    return Conversation(agency_id=client.agency_id, client_id=client.id, agent_id=agent.id, **fields)


def _customer(client: TestClient, name: str) -> tuple[dict, dict]:
    customer = client.post("/api/clients", json={"name": name, "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post(
        "/api/agents",
        json={"client_id": customer["id"], "provider": "openrouter", "model": "gpt-4.1-mini",
              "name": "Vera", "instructions": "", "personality": "", "is_active": True},
    ).json()
    return customer, agent


def _portal_login(client: TestClient, customer: dict) -> str:
    slug = customer["portal_slug"]
    email = f"ana@{slug}.com"
    client.post(f"/api/clients/{customer['id']}/portal-users", json={"name": "Ana", "email": email, "password": "secure-portal"})
    client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_enabled": True})
    assert client.post(f"/api/portal/{slug}/login", json={"email": email, "password": "secure-portal"}).status_code == 200
    return slug


# Counting -------------------------------------------------------------------


def test_numbers_count_up_per_client_without_gaps():
    with TestingSession() as db:
        first, first_agent = _seed(db, "a")
        second, second_agent = _seed(db, "b")
        rows = [_conversation(first, first_agent) for _ in range(3)]
        rows += [_conversation(second, second_agent)]
        rows += [_conversation(first, first_agent)]
        db.add_all(rows)
        db.commit()
        assert [row.number for row in rows] == [1, 2, 3, 1, 4]
        # The counter is bumped in SQL, so the session's copy of the client is stale.
        db.refresh(first)
        db.refresh(second)
        assert (first.conversation_seq, second.conversation_seq) == (4, 1)

        # A later, separate transaction carries on from the counter.
        later = _conversation(second, second_agent)
        db.add(later)
        db.commit()
        assert later.number == 2


def test_a_rolled_back_insert_leaves_no_gap():
    with TestingSession() as db:
        client, agent = _seed(db)
        db.commit()
        db.add(_conversation(client, agent))
        db.commit()

        rolled_back = _conversation(client, agent)
        db.add(rolled_back)
        db.flush()
        assert rolled_back.number == 2
        db.rollback()

        kept = _conversation(client, agent)
        db.add(kept)
        db.commit()
        assert kept.number == 2


def test_conversations_created_by_the_app_are_numbered(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    customer, agent = _customer(client, "Numbers Co")

    # Panel creation (playground).
    created = client.post("/api/conversations", json={"agent_id": agent["id"]}).json()
    assert created["number"] == 1

    # WhatsApp inbound pipeline.
    channel = client.put(f"/api/whatsapp/channels/{customer['id']}", json={"agent_id": agent["id"]}).json()
    monkeypatch.setattr(whatsapp_inbound_service, "run_completion", AsyncMock(return_value=ai_service.Completion(text="Hi")))
    monkeypatch.setattr(evolution_driver, "send_text", AsyncMock(return_value="wa-out"))
    monkeypatch.setattr(evolution_driver, "mark_read", AsyncMock())
    inbound = client.post(
        f"/api/internal/whatsapp/channels/{channel['id']}/inbound",
        headers={"X-Bridge-Token": get_settings().whatsapp_bridge_token},
        json={"external_message_id": "m1", "remote_jid": "573001112233@s.whatsapp.net", "sender_name": "Sam", "text": "Hola"},
    )
    assert inbound.status_code == 200, inbound.text
    from_whatsapp = client.get(f"/api/conversations/{inbound.json()['conversation_id']}").json()
    assert from_whatsapp["number"] == 2

    # Quick lead (pipeline).
    lead = client.post(f"/api/clients/{customer['id']}/pipeline/leads", json={"contact_name": "Lead One"})
    assert lead.status_code == 201, lead.text
    assert lead.json()["number"] == 3
    board = client.get(f"/api/clients/{customer['id']}/pipeline/board").json()
    assert {card["number"] for card in board["cards"]} == {1, 2, 3}

    # A second client starts again at 1.
    other, other_agent = _customer(client, "Other Co")
    assert client.post("/api/conversations", json={"agent_id": other_agent["id"]}).json()["number"] == 1


def test_widget_conversations_are_numbered(authenticated_client: TestClient, monkeypatch):
    from app.routers import widget as widget_router

    client = authenticated_client
    customer, agent = _customer(client, "Widget Co")
    channel = client.put(f"/api/webchat/channels/{customer['id']}", json={"agent_id": agent["id"], "is_enabled": True})
    assert channel.status_code == 200, channel.text
    public_id = channel.json()["public_id"]
    monkeypatch.setattr(widget_router, "run_completion", AsyncMock(return_value=ai_service.Completion(text="Hola")))
    for session in ("s1", "s2"):
        assert client.post(f"/api/widget/{public_id}/messages", json={"session_id": session, "content": "hola"}).status_code == 200
    with TestingSession() as db:
        numbers = sorted(db.scalars(select(Conversation.number).where(Conversation.client_id == uuid.UUID(customer["id"]))).all())
    assert numbers == [1, 2]


# By-number routes -------------------------------------------------------------


def test_portal_and_panel_read_a_conversation_by_number(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent = _customer(client, "Route Co")
    other, other_agent = _customer(client, "Other Co")
    slug = _portal_login(client, customer)
    first = customer_conversation(client, agent["id"])
    second = customer_conversation(client, agent["id"])
    foreign = customer_conversation(client, other_agent["id"])
    assert (first["number"], second["number"], foreign["number"]) == (1, 2, 1)

    portal = client.get(f"/api/portal/{slug}/conversations/number/2")
    assert portal.status_code == 200, portal.text
    assert portal.json()["id"] == second["id"] and portal.json()["number"] == 2
    # Exactly what the by-id route answers.
    assert portal.json() == client.get(f"/api/portal/{slug}/conversations/{second['id']}").json()
    assert client.get(f"/api/portal/{slug}/conversations/number/1").json()["id"] == first["id"]
    assert client.get(f"/api/portal/{slug}/conversations/number/3").status_code == 404
    assert client.get(f"/api/portal/{slug}/conversations/number/0").status_code == 404

    panel = client.get(f"/api/clients/{customer['id']}/conversations/number/2")
    assert panel.status_code == 200, panel.text
    assert panel.json()["id"] == second["id"] and panel.json()["number"] == 2
    assert panel.json() == client.get(f"/api/conversations/{second['id']}").json()
    # Each client has its own #1.
    assert client.get(f"/api/clients/{other['id']}/conversations/number/1").json()["id"] == foreign["id"]
    assert client.get(f"/api/clients/{customer['id']}/conversations/number/3").status_code == 404
    assert client.get(f"/api/clients/{customer['id']}/conversations/number/abc").status_code == 422


def test_by_number_never_reaches_another_client_or_agency(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent = _customer(client, "Mine Co")
    slug = _portal_login(client, customer)
    mine = customer_conversation(client, agent["id"])
    with TestingSession() as db:
        foreign_client, foreign_agent = _seed(db, "foreign")
        db.add_all([_conversation(foreign_client, foreign_agent) for _ in range(3)])
        db.commit()
        foreign_client_id = str(foreign_client.id)

    # Number 2 exists only in the other agency.
    assert client.get(f"/api/clients/{customer['id']}/conversations/number/2").status_code == 404
    assert client.get(f"/api/portal/{slug}/conversations/number/2").status_code == 404
    # Asking through the other agency's client id finds nothing either, even for #1.
    assert client.get(f"/api/clients/{foreign_client_id}/conversations/number/1").status_code == 404
    assert client.get(f"/api/clients/{customer['id']}/conversations/number/1").json()["id"] == mine["id"]


# Output -----------------------------------------------------------------------


def test_number_is_in_every_conversation_payload(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent = _customer(client, "Payload Co")
    slug = _portal_login(client, customer)
    conversation = customer_conversation(client, agent["id"])
    base = f"/api/portal/{slug}"

    assert client.get(f"{base}/conversations").json()[0]["number"] == 1
    assert client.get(f"{base}/inbox").json()["items"][0]["number"] == 1
    assert client.get(f"{base}/conversations/{conversation['id']}").json()["number"] == 1
    assert client.get(f"/api/conversations/{conversation['id']}").json()["number"] == 1
    assert client.get("/api/conversations").json()[0]["number"] == 1
    assert client.get("/api/conversations/inbox").json()[0]["number"] == 1


def test_api_v1_exposes_number_and_html_link(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent = _customer(client, "V1 Co")
    integration = client.post("/api/integrations", json={"name": "n8n", "scopes": ["inbox.read", "pipeline.read", "pipeline.manage"]}).json()
    token = client.post(f"/api/integrations/{integration['id']}/tokens", json={"expires_in_days": 30}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    conversation = customer_conversation(client, agent["id"])
    expected = f"{get_settings().frontend_url.rstrip('/')}/clients/{customer['id']}/inbox/1"

    one = client.get(f"/api/v1/clients/{customer['id']}/conversations/{conversation['id']}", headers=headers).json()
    assert one["number"] == 1
    assert one["_links"]["html"] == expected
    assert one["_links"]["self"].endswith(conversation["id"])
    listed = client.get(f"/api/v1/clients/{customer['id']}/conversations", headers=headers).json()
    assert listed["data"][0]["number"] == 1 and listed["data"][0]["_links"]["html"] == expected

    board = client.get(f"/api/v1/clients/{customer['id']}/pipeline/board", headers=headers).json()
    assert board["cards"][0]["number"] == 1
    lead = client.post(f"/api/v1/clients/{customer['id']}/pipeline/leads", headers=headers, json={"contact_name": "Lead"})
    assert lead.status_code == 201, lead.text
    assert lead.json()["number"] == 2


def test_webhook_payloads_carry_the_number(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    customer, agent = _customer(client, "Hook Co")
    channel = client.put(f"/api/whatsapp/channels/{customer['id']}", json={"agent_id": agent["id"]}).json()
    monkeypatch.setattr(evolution_driver, "send_text", AsyncMock(return_value="wa-out"))
    monkeypatch.setattr(evolution_driver, "mark_read", AsyncMock())
    monkeypatch.setattr(whatsapp_inbound_service, "run_completion", AsyncMock(return_value=ai_service.Completion(text="Hi")))
    integration = client.post("/api/integrations", json={"name": "n8n", "scopes": ["inbox.read"]}).json()
    subscribed = client.post(f"/api/integrations/{integration['id']}/webhooks", json={
        "url": "https://n8n.example/hook", "events": ["message.received", "conversation.resolved", "deal.moved"]})
    assert subscribed.status_code == 201, subscribed.text

    # A first conversation so the one under test is #2.
    client.post("/api/conversations", json={"agent_id": agent["id"]})
    inbound = client.post(
        f"/api/internal/whatsapp/channels/{channel['id']}/inbound",
        headers={"X-Bridge-Token": get_settings().whatsapp_bridge_token},
        json={"external_message_id": "m1", "remote_jid": "573001112233@s.whatsapp.net", "sender_name": "Sam", "text": "Hola"},
    )
    conversation_id = inbound.json()["conversation_id"]
    stage = client.post(f"/api/clients/{customer['id']}/pipeline/stages", json={"name": "New"}).json()
    assert client.patch(f"/api/conversations/{conversation_id}/pipeline", json={"pipeline_stage_id": stage["id"]}).status_code == 200
    assert client.patch(f"/api/conversations/{conversation_id}/status", json={"status": "resolved"}).status_code == 200

    with TestingSession() as db:
        payloads = {row.event: row.payload for row in db.scalars(select(WebhookDelivery)).all()}
    assert set(payloads) == {"message.received", "conversation.resolved", "deal.moved"}
    for payload in payloads.values():
        assert payload["conversation_id"] == conversation_id
        assert payload["number"] == 2


# Migration --------------------------------------------------------------------


def _run_migration(function_name: str) -> None:
    spec = importlib.util.spec_from_file_location("migration_0058", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with test_engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            getattr(module, function_name)()


def _columns(table: str) -> set[str]:
    with test_engine.connect() as connection:
        rows = connection.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": table}
        )
        return {row[0] for row in rows}


def test_upgrade_numbers_existing_rows_oldest_first_per_client_and_seeds_the_counters():
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with TestingSession() as db:
        first, first_agent = _seed(db, "a")
        second, second_agent = _seed(db, "b")
        empty, _ = _seed(db, "c")
        # Inserted newest first, so insertion order (the numbers the hook hands out
        # now) is the opposite of the age order the migration must produce.
        a_new = _conversation(first, first_agent, created_at=base + timedelta(days=3))
        a_old = _conversation(first, first_agent, created_at=base + timedelta(days=1))
        a_mid = _conversation(first, first_agent, created_at=base + timedelta(days=2))
        b_only = _conversation(second, second_agent, created_at=base)
        db.add_all([a_new, a_old, a_mid, b_only])
        db.commit()
        ids = {"new": a_new.id, "old": a_old.id, "mid": a_mid.id, "b": b_only.id}
        client_ids = (first.id, second.id, empty.id)
    assert [_number(ids[key]) for key in ("new", "old", "mid")] == [1, 2, 3]

    # Put the schema back the way 0057 left it: no number, no counter.
    with test_engine.begin() as connection:
        connection.execute(text("ALTER TABLE conversations DROP COLUMN number"))
        connection.execute(text("ALTER TABLE clients DROP COLUMN conversation_seq"))
    assert "number" not in _columns("conversations") and "conversation_seq" not in _columns("clients")

    _run_migration("upgrade")

    assert "number" in _columns("conversations") and "conversation_seq" in _columns("clients")
    with test_engine.connect() as connection:
        numbers = {
            key: connection.execute(text("SELECT number FROM conversations WHERE id = :id"), {"id": ids[key]}).scalar()
            for key in ids
        }
        seqs = [
            connection.execute(text("SELECT conversation_seq FROM clients WHERE id = :id"), {"id": cid}).scalar()
            for cid in client_ids
        ]
        nullable = connection.execute(
            text("SELECT is_nullable FROM information_schema.columns WHERE table_name = 'conversations' AND column_name = 'number'")
        ).scalar()
    assert numbers == {"old": 1, "mid": 2, "new": 3, "b": 1}
    assert seqs == [3, 1, 0]
    assert nullable == "NO"

    # The unique constraint is in place and the hook carries on from the counter.
    with TestingSession() as db:
        client = db.get(Client, client_ids[0])
        agent = db.scalar(select(Agent).where(Agent.client_id == client.id))
        fresh = _conversation(client, agent)
        db.add(fresh)
        db.commit()
        assert fresh.number == 4
        db.add(_conversation(client, agent, number=4))
        with pytest.raises(Exception):
            db.commit()
        db.rollback()

    _run_migration("downgrade")
    assert "number" not in _columns("conversations") and "conversation_seq" not in _columns("clients")
    # ... and the way back is repeatable.
    _run_migration("upgrade")
    assert "number" in _columns("conversations")


def _number(conversation_id) -> int:
    with TestingSession() as db:
        return db.get(Conversation, conversation_id).number


# Concurrency ---------------------------------------------------------------------


def test_concurrent_inserts_for_one_client_get_distinct_numbers():
    """Real parallel sessions on the test database: 6 threads, 8 conversations each."""
    with TestingSession() as db:
        client, agent = _seed(db)
        db.commit()
        client_id, agency_id, agent_id = client.id, client.agency_id, agent.id

    threads_count, per_thread = 6, 8
    barrier = threading.Barrier(threads_count)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            for _ in range(per_thread):
                with TestingSession() as db:
                    db.add(Conversation(agency_id=agency_id, client_id=client_id, agent_id=agent_id))
                    db.commit()
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(threads_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors

    total = threads_count * per_thread
    with TestingSession() as db:
        numbers = sorted(db.scalars(select(Conversation.number).where(Conversation.client_id == client_id)).all())
        assert numbers == list(range(1, total + 1))
        assert db.get(Client, client_id).conversation_seq == total


def test_a_second_insert_waits_for_the_first_transactions_row_lock():
    """The lock is what serializes writers: while one transaction holds its number,
    another insert for the same client blocks until it commits, and a different
    client is not held up."""
    with TestingSession() as db:
        client, agent = _seed(db, "a")
        other, other_agent = _seed(db, "b")
        db.commit()
        ids = (client.id, client.agency_id, agent.id, other.id, other.agency_id, other_agent.id)
    client_id, agency_id, agent_id, other_id, other_agency_id, other_agent_id = ids

    holder = TestingSession()
    first = Conversation(agency_id=agency_id, client_id=client_id, agent_id=agent_id)
    holder.add(first)
    holder.flush()  # number 1 is taken and the client row is locked, not committed
    assert first.number == 1

    result: dict = {}
    finished = threading.Event()

    def contender() -> None:
        with TestingSession() as db:
            row = Conversation(agency_id=agency_id, client_id=client_id, agent_id=agent_id)
            db.add(row)
            db.commit()
            result["number"] = row.number
        finished.set()

    thread = threading.Thread(target=contender)
    thread.start()
    try:
        # Another client's insert does not wait on this lock.
        with TestingSession() as db:
            row = Conversation(agency_id=other_agency_id, client_id=other_id, agent_id=other_agent_id)
            db.add(row)
            db.commit()
            assert row.number == 1
        assert not finished.wait(timeout=1.0), "the second insert should be waiting on the first one's row lock"
        holder.commit()
        assert finished.wait(timeout=10)
    finally:
        holder.close()
        thread.join(timeout=10)
    assert result["number"] == 2
