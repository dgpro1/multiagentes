"""The platform sets an agency's line quotas; the agency assigns them to its clients."""

import uuid

from fastapi.testclient import TestClient

from app import channel_quotas
from app.models import Agency, Client
from conftest import TestingSession
from test_platform_auth import _login, _platform_admin


def _platform(client: TestClient) -> None:
    _platform_admin()
    _login(client)


def _agency_id(client: TestClient) -> str:
    return client.get("/api/agency").json()["id"]


def _client(client: TestClient, name: str = "Acme") -> dict:
    return client.post("/api/clients", json={"name": name, "is_active": True}).json()


def _client_and_agent(client: TestClient, name: str = "Acme") -> tuple[dict, dict]:
    customer = _client(client, name)
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post("/api/agents", json={
        "client_id": customer["id"], "provider": "openrouter", "model": "openai/gpt-5.6-luna",
        "name": f"Bot {name}", "instructions": "", "personality": "", "is_active": True}).json()
    return customer, agent


def _put_quotas(client: TestClient, agency_id: str, quotas: dict, **extra) -> object:
    return client.put(f"/api/platform/agencies/{agency_id}/features", json={"features": {}, "channel_quotas": quotas, **extra})


# --- The platform side -------------------------------------------------------

def test_the_platform_sets_and_lifts_a_quota(authenticated_client):
    client = authenticated_client
    agency_id = _agency_id(client)
    _platform(client)
    saved = _put_quotas(client, agency_id, {"channels.whatsapp": 5, "channels.instagram": 1})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["channel_quotas"] == {"channels.whatsapp": 5, "channels.instagram": 1}
    # null lifts the cap and drops the key, which is what "unlimited" means.
    lifted = _put_quotas(client, agency_id, {"channels.whatsapp": None})
    assert lifted.status_code == 200, lifted.text
    assert lifted.json()["channel_quotas"] == {"channels.instagram": 1}


def test_omitting_the_quotas_leaves_them_alone(authenticated_client):
    client = authenticated_client
    agency_id = _agency_id(client)
    _platform(client)
    _put_quotas(client, agency_id, {"channels.whatsapp": 5})
    # The Plan tab saves a switch without rewriting the numbers.
    only_switch = client.put(f"/api/platform/agencies/{agency_id}/features", json={"features": {"agents": False}})
    assert only_switch.status_code == 200, only_switch.text
    assert only_switch.json()["channel_quotas"] == {"channels.whatsapp": 5}


def test_the_numbers_are_validated(authenticated_client):
    client = authenticated_client
    agency_id = _agency_id(client)
    _platform(client)
    assert _put_quotas(client, agency_id, {"nonsense": 1}).status_code == 422
    assert _put_quotas(client, agency_id, {"channels.whatsapp": -1}).status_code == 422
    assert _put_quotas(client, agency_id, {"channels.whatsapp": 1000}).status_code == 422
    assert _put_quotas(client, agency_id, {"channels.whatsapp": True}).status_code == 422
    assert _put_quotas(client, agency_id, {"channels.whatsapp": 2.5}).status_code == 422
    assert _put_quotas(client, agency_id, {"channels.whatsapp": "5"}).status_code == 422


def test_every_change_is_audited_with_both_states(authenticated_client):
    client = authenticated_client
    agency_id = _agency_id(client)
    _platform(client)
    _put_quotas(client, agency_id, {"channels.whatsapp": 5})
    _put_quotas(client, agency_id, {"channels.whatsapp": 10})
    events = [event for event in client.get("/api/platform/audit-events").json() if event["action"] == "agency.features_changed"]
    details = events[0]["details"]
    assert details["previous_channel_quotas"] == {"channels.whatsapp": 5}
    assert details["channel_quotas"] == {"channels.whatsapp": 10}


def test_the_catalog_lists_the_types_that_take_a_number(authenticated_client):
    client = authenticated_client
    _platform_admin()
    _login(client)
    catalog = client.get("/api/platform/features").json()
    keys = [entry["key"] for entry in catalog["quotas"]]
    assert keys == list(channel_quotas.CATALOG)
    assert all(entry["max"] == channel_quotas.MAX_QUOTA for entry in catalog["quotas"])
    assert catalog["quotas"][0]["label"] == "WhatsApp QR"


def test_usage_reports_what_is_connected_and_how_it_was_spread(authenticated_client):
    client = authenticated_client
    agency_id = _agency_id(client)
    customer, agent = _client_and_agent(client, "Acme")
    customer2, agent2 = _client_and_agent(client, "Dos")
    from app.models import WhatsAppChannel

    with TestingSession() as db:
        for _ in range(2):
            db.add(WhatsAppChannel(agency_id=uuid.UUID(agency_id), client_id=uuid.UUID(customer["id"]),
                                   agent_id=uuid.UUID(agent["id"])))
        db.add(WhatsAppChannel(agency_id=uuid.UUID(agency_id), client_id=uuid.UUID(customer2["id"]),
                               agent_id=uuid.UUID(agent2["id"])))
        db.commit()
    _platform(client)
    _put_quotas(client, agency_id, {"channels.whatsapp": 5})
    usage = client.get(f"/api/platform/agencies/{agency_id}/usage").json()
    whatsapp = next(row for row in usage["channels"] if row["key"] == "channels.whatsapp")
    assert whatsapp["used"] == 3
    assert whatsapp["quota"] == 5
    assert {row["client_name"]: row["used"] for row in whatsapp["by_client"]} == {"Acme": 2, "Dos": 1}
    # A type nobody capped and nobody uses is still reported, as unlimited and empty.
    empty = next(row for row in usage["channels"] if row["key"] == "channels.messenger")
    assert empty == {"key": "channels.messenger", "label": "Messenger", "used": 0, "quota": None, "by_client": []}


def test_the_platform_also_reads_a_client_share(authenticated_client):
    client = authenticated_client
    agency_id = _agency_id(client)
    customer = _client(client)
    with TestingSession() as db:
        row = db.get(Client, uuid.UUID(customer["id"]))
        row.channel_allocations = {"channels.whatsapp": 2}
        db.commit()
    _platform(client)
    listed = client.get(f"/api/platform/agencies/{agency_id}/clients").json()
    assert listed[0]["allocations"] == {"channels.whatsapp": 2}


# --- The agency side ---------------------------------------------------------

def test_the_agency_assigns_lines_to_a_client(authenticated_client):
    client = authenticated_client
    agency_id = _agency_id(client)
    customer = _client(client)
    with TestingSession() as db:
        agency = db.get(Agency, uuid.UUID(agency_id))
        agency.channel_quotas = {"channels.whatsapp": 5}
        db.commit()
    before = client.get(f"/api/clients/{customer['id']}/channel-allowances")
    assert before.status_code == 200, before.text
    whatsapp = next(row for row in before.json() if row["key"] == "channels.whatsapp")
    assert (whatsapp["agency_quota"], whatsapp["allocation"], whatsapp["used"], whatsapp["remaining"]) == (5, None, 0, 5)

    saved = client.put(f"/api/clients/{customer['id']}/channel-allowances", json={"allocations": {"channels.whatsapp": 2}})
    assert saved.status_code == 200, saved.text
    whatsapp = next(row for row in saved.json() if row["key"] == "channels.whatsapp")
    assert (whatsapp["allocation"], whatsapp["allowed"], whatsapp["remaining"]) == (2, 2, 2)
    with TestingSession() as db:
        assert db.get(Client, uuid.UUID(customer["id"])).channel_allocations == {"channels.whatsapp": 2}


def test_an_allocation_above_the_plan_is_refused(authenticated_client):
    client = authenticated_client
    agency_id = _agency_id(client)
    customer = _client(client)
    with TestingSession() as db:
        agency = db.get(Agency, uuid.UUID(agency_id))
        agency.channel_quotas = {"channels.whatsapp": 2}
        db.commit()
    refused = client.put(f"/api/clients/{customer['id']}/channel-allowances", json={"allocations": {"channels.whatsapp": 3}})
    assert refused.status_code == 422
    assert "allows 2" in refused.json()["detail"]


def test_the_agency_may_promise_more_than_the_pool_holds(authenticated_client):
    client = authenticated_client
    agency_id = _agency_id(client)
    first = _client(client, "Uno")
    second = _client(client, "Dos")
    with TestingSession() as db:
        agency = db.get(Agency, uuid.UUID(agency_id))
        agency.channel_quotas = {"channels.whatsapp": 3}
        db.commit()
    # Planning, not a mistake: the pool is what limits what can connect.
    assert client.put(f"/api/clients/{first['id']}/channel-allowances", json={"allocations": {"channels.whatsapp": 3}}).status_code == 200
    assert client.put(f"/api/clients/{second['id']}/channel-allowances", json={"allocations": {"channels.whatsapp": 3}}).status_code == 200


def test_lifting_an_allocation_and_validating_unknown_keys(authenticated_client):
    client = authenticated_client
    customer = _client(client)
    assert client.put(f"/api/clients/{customer['id']}/channel-allowances", json={"allocations": {"channels.whatsapp": 2}}).status_code == 200
    lifted = client.put(f"/api/clients/{customer['id']}/channel-allowances", json={"allocations": {"channels.whatsapp": None}})
    assert lifted.status_code == 200, lifted.text
    whatsapp = next(row for row in lifted.json() if row["key"] == "channels.whatsapp")
    assert whatsapp["allocation"] is None
    assert client.put(f"/api/clients/{customer['id']}/channel-allowances", json={"allocations": {"nope": 1}}).status_code == 422
    assert client.put(f"/api/clients/{customer['id']}/channel-allowances", json={"allocations": {"channels.whatsapp": -2}}).status_code == 422


def test_another_agencys_client_is_not_reachable(authenticated_client):
    client = authenticated_client
    from test_multi_agency_isolation import _as_b, _client_a

    customer = _client_a(client)
    _as_b(client)
    assert client.get(f"/api/clients/{customer['id']}/channel-allowances").status_code == 404
    assert client.put(f"/api/clients/{customer['id']}/channel-allowances", json={"allocations": {"channels.whatsapp": 1}}).status_code == 404
