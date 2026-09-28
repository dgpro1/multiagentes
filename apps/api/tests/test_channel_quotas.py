"""Channel quotas: the agency's plan, the client's share, and what happens at the edge.

The quotas themselves are set through the platform API (covered in
test_platform_channel_quotas.py); here they are written straight to the columns so
these tests are about the ceiling and nothing else.
"""

import pathlib
import re
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import channel_quotas
from app.models import Agency, Client, SocialChannel, WhatsAppChannel
from app.services import evolution
from conftest import TestingSession


def _agency_id(client: TestClient) -> str:
    return client.get("/api/agency").json()["id"]


def _client_with_agent(client: TestClient, name: str = "Acme") -> tuple[dict, dict]:
    customer = client.post("/api/clients", json={"name": name, "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post("/api/agents", json={
        "client_id": customer["id"], "provider": "openrouter", "model": "openai/gpt-5.6-luna",
        "name": "Bot", "instructions": "", "personality": "", "is_active": True}).json()
    return customer, agent


def _add_line(client: TestClient, customer: dict, agent: dict):
    return client.post(f"/api/whatsapp/clients/{customer['id']}/channels", json={"agent_id": agent["id"]})


def _set_quota(agency_id: str, key: str, value: int | None) -> None:
    with TestingSession() as db:
        agency = db.get(Agency, uuid.UUID(agency_id))
        agency.channel_quotas = {} if value is None else {key: value}
        db.commit()


def _set_allocation(client_id: str, key: str, value: int | None) -> None:
    with TestingSession() as db:
        customer = db.get(Client, uuid.UUID(client_id))
        customer.channel_allocations = {} if value is None else {key: value}
        db.commit()


# --- The defaults ------------------------------------------------------------

def test_quotas_are_unlimited_until_the_platform_sets_one(authenticated_client):
    client = authenticated_client
    customer, agent = _client_with_agent(client)
    for _ in range(3):
        assert _add_line(client, customer, agent).status_code == 201


def test_a_missing_key_means_unlimited_and_an_unknown_key_is_ignored():
    assert channel_quotas.normalize(None)["channels.whatsapp"] is None
    assert channel_quotas.normalize({"channels.whatsapp": 5})["channels.whatsapp"] == 5
    # Hand-edited or older rows can never leak into a decision.
    assert channel_quotas.normalize({"nonsense": 4, "channels.whatsapp": "many"})["channels.whatsapp"] is None


# --- The agency's plan -------------------------------------------------------

def test_the_agency_quota_refuses_the_line_that_exceeds_the_plan(authenticated_client):
    client = authenticated_client
    customer, agent = _client_with_agent(client)
    _set_quota(_agency_id(client), "channels.whatsapp", 2)
    assert _add_line(client, customer, agent).status_code == 201
    assert _add_line(client, customer, agent).status_code == 201
    refused = _add_line(client, customer, agent)
    assert refused.status_code == 409
    assert "raise the plan" in refused.json()["detail"]


def test_zero_means_this_type_is_not_included(authenticated_client):
    client = authenticated_client
    customer, agent = _client_with_agent(client)
    _set_quota(_agency_id(client), "channels.whatsapp", 0)
    refused = _add_line(client, customer, agent)
    assert refused.status_code == 409
    assert "raise the plan" in refused.json()["detail"]


def test_the_plan_counts_the_whole_agency_not_each_client(authenticated_client):
    client = authenticated_client
    first, first_agent = _client_with_agent(client, "Uno")
    second, second_agent = _client_with_agent(client, "Dos")
    _set_quota(_agency_id(client), "channels.whatsapp", 1)
    assert _add_line(client, first, first_agent).status_code == 201
    # Another client of the same agency cannot take a line the plan does not have.
    assert _add_line(client, second, second_agent).status_code == 409


# --- The client's share ------------------------------------------------------

def test_a_client_allocation_caps_it_before_the_agency_pool(authenticated_client):
    client = authenticated_client
    first, first_agent = _client_with_agent(client, "Uno")
    second, second_agent = _client_with_agent(client, "Dos")
    _set_quota(_agency_id(client), "channels.whatsapp", 5)
    _set_allocation(first["id"], "channels.whatsapp", 1)
    assert _add_line(client, first, first_agent).status_code == 201
    refused = _add_line(client, first, first_agent)
    assert refused.status_code == 409
    assert "assigned to it" in refused.json()["detail"]
    # The client without an allocation of its own still draws on the pool.
    assert _add_line(client, second, second_agent).status_code == 201


def test_an_allocation_above_the_pool_still_meets_the_pool(authenticated_client):
    client = authenticated_client
    customer, agent = _client_with_agent(client)
    _set_quota(_agency_id(client), "channels.whatsapp", 1)
    _set_allocation(customer["id"], "channels.whatsapp", 9)
    assert _add_line(client, customer, agent).status_code == 201
    assert _add_line(client, customer, agent).status_code == 409


# --- What does not consume a slot -------------------------------------------

def test_deleting_a_line_frees_its_slot(authenticated_client):
    client = authenticated_client
    customer, agent = _client_with_agent(client)
    _set_quota(_agency_id(client), "channels.whatsapp", 1)
    line = _add_line(client, customer, agent).json()
    assert client.delete(f"/api/whatsapp/channels/{line['id']}").status_code == 204
    assert _add_line(client, customer, agent).status_code == 201


def test_reconfiguring_an_existing_line_never_consumes_a_new_slot(authenticated_client):
    client = authenticated_client
    customer, agent = _client_with_agent(client)
    _set_quota(_agency_id(client), "channels.whatsapp", 1)
    line = _add_line(client, customer, agent).json()
    again = client.put(f"/api/whatsapp/channels/{line['id']}", json={"agent_id": agent["id"], "label": "Recepción"})
    assert again.status_code == 200, again.text
    assert again.json()["label"] == "Recepción"


# --- The other channel types -------------------------------------------------

def test_whatsapp_api_lines_are_counted_separately(authenticated_client):
    client = authenticated_client
    customer, agent = _client_with_agent(client)
    _set_quota(_agency_id(client), "channels.whatsapp_cloud", 1)
    payload = {"agent_id": agent["id"]}
    assert client.post(f"/api/whatsapp-cloud/clients/{customer['id']}/channels", json=payload).status_code == 201
    assert client.post(f"/api/whatsapp-cloud/clients/{customer['id']}/channels", json=payload).status_code == 409
    # The QR plan is untouched by it.
    assert _add_line(client, customer, agent).status_code == 201


def test_the_web_chat_is_counted_too(authenticated_client):
    client = authenticated_client
    customer, agent = _client_with_agent(client)
    _set_quota(_agency_id(client), "channels.webchat", 0)
    refused = client.put(f"/api/webchat/channels/{customer['id']}", json={
        "agent_id": agent["id"], "is_enabled": True, "greeting": "", "color": "#0d9488", "position": "right"})
    assert refused.status_code == 409
    assert "raise the plan" in refused.json()["detail"]


def test_instagram_and_messenger_count_their_own_provider(authenticated_client):
    client = authenticated_client
    agency_id = uuid.UUID(_agency_id(client))
    customer, agent = _client_with_agent(client)
    with TestingSession() as db:
        for provider_name in ("instagram", "messenger"):
            db.add(SocialChannel(agency_id=agency_id, client_id=uuid.UUID(customer["id"]), agent_id=uuid.UUID(agent["id"]),
                                 provider=provider_name, webhook_verify_token=f"tok-{provider_name}"))
        db.commit()
    with TestingSession() as db:
        assert channel_quotas.used_by_agency(db, agency_id, "channels.instagram") == 1
        assert channel_quotas.used_by_agency(db, agency_id, "channels.messenger") == 1
        assert channel_quotas.used_by_agency(db, agency_id, "channels.whatsapp") == 0


# --- Which Evolution deployment a line lives on ------------------------------

def _env(monkeypatch, **values):
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    from app.config import get_settings

    get_settings.cache_clear()


def test_the_endpoint_pool_spreads_new_lines(monkeypatch, authenticated_client):
    _env(monkeypatch, EVOLUTION_API_KEY="k", EVOLUTION_API_URL="http://main", EVOLUTION_API_URLS="http://main,http://second")
    client = authenticated_client
    customer, agent = _client_with_agent(client)
    for _ in range(3):
        assert _add_line(client, customer, agent).status_code == 201
    with TestingSession() as db:
        recorded = [row.evolution_endpoint for row in db.scalars(select(WhatsAppChannel).order_by(WhatsAppChannel.created_at))]
    assert recorded == ["http://main", "http://second", "http://main"]


def test_a_line_uses_the_endpoint_it_recorded(monkeypatch):
    _env(monkeypatch, EVOLUTION_API_KEY="k", EVOLUTION_API_URL="http://main")
    assert evolution.endpoint_for(SimpleNamespace(evolution_endpoint="http://second")) == "http://second"
    # Empty is what every line created before the column existed holds.
    assert evolution.endpoint_for(SimpleNamespace(evolution_endpoint="")) == "http://main"
    assert evolution.endpoint_for(None) == "http://main"
    assert evolution.endpoints() == ["http://main"]


def test_endpoints_are_deduped_and_trimmed(monkeypatch):
    _env(monkeypatch, EVOLUTION_API_KEY="k", EVOLUTION_API_URL="http://main/", EVOLUTION_API_URLS="http://second, http://main")
    assert sorted(evolution.endpoints()) == ["http://main", "http://second"]


# --- The migration ------------------------------------------------------------

def test_the_migration_writes_empty_objects_as_server_defaults():
    source = (pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0078_channel_quotas.py").read_text(encoding="utf-8")
    defaults = re.findall(r'server_default="(\{\})"', source)
    # Two JSON columns default to "{}": an absent key means unlimited, so the
    # upgrade changes nothing for an installation that never sets a quota.
    assert len(defaults) == 2
    assert "channel_quotas" in source and "channel_allocations" in source and "evolution_endpoint" in source


def test_the_catalog_matches_the_channel_modules():
    from app import agency_features

    assert set(channel_quotas.CATALOG) <= set(agency_features.KEYS)
    assert all(key.startswith("channels.") for key in channel_quotas.CATALOG)


def test_the_typescript_mirror_lists_the_same_countable_types():
    """The platform's Plan tab keeps its own list — it must not grow a quota row
    for a channel the API cannot count — so the two are compared here."""
    source = (pathlib.Path(__file__).resolve().parents[2] / "web" / "lib" / "agency-features.ts").read_text(encoding="utf-8")
    block = source.split("export const QUOTA_FEATURES", 1)
    assert len(block) == 2, "could not find QUOTA_FEATURES in agency-features.ts"
    listed = re.findall(r'"(channels\.[a-z_]+)"', block[1].split("] as const", 1)[0])
    assert listed == list(channel_quotas.CATALOG), "apps/web/lib/agency-features.ts and app/channel_quotas.py drifted"
