"""Phase 1: two agencies see nothing of each other, on every agency-panel path.

Agency A is born through the panel's own first-run setup (``authenticated_client``);
Agency B is seeded the way data created by older releases exists (``login_legacy_owner``).
A gets real resources; B — as a signed-in person and as an API token — must be answered
404 wherever it touches them, exactly as if the resources did not exist.

The file also runs under ``HUNTERAI_TENANT_TESTS=1``, where every client's data plane
lives in its own database, so the same boundaries must hold across engines, not just
across ``agency_id`` filters.
"""

from fastapi.testclient import TestClient

from conftest import login_legacy_owner

A_CREDENTIALS = {"email": "ana@prisma.com", "password": "contrasena-segura"}


def _as_a(client: TestClient) -> None:
    response = client.post("/api/auth/login", json=A_CREDENTIALS)
    assert response.status_code == 200, response.text


def _as_b(client: TestClient) -> dict:
    return login_legacy_owner(client)


def _client_a(client: TestClient) -> dict:
    response = client.post("/api/clients", json={"name": "Cliente A", "is_active": True})
    assert response.status_code == 201, response.text
    return response.json()


def _agent_a(client: TestClient, client_id: str) -> dict:
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    response = client.post(
        "/api/agents",
        json={"client_id": client_id, "provider": "openrouter", "model": "openai/gpt-5.6-luna",
              "name": "Agente A", "instructions": "", "is_active": True},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _conversation_a(client: TestClient, agent_id: str) -> dict:
    response = client.post("/api/conversations", json={"agent_id": agent_id})
    assert response.status_code == 201, response.text
    return response.json()


def test_each_agency_lists_only_its_own_clients_and_agents(authenticated_client):
    client = authenticated_client
    customer = _client_a(client)
    agent = _agent_a(client, customer["id"])
    _as_b(client)
    listed = client.get("/api/clients").json()
    assert customer["id"] not in {item["id"] for item in listed}
    listed_agents = client.get("/api/agents").json()
    assert agent["id"] not in {item["id"] for item in listed_agents}


def test_clients_agents_and_conversations_are_invisible_across_agencies(authenticated_client):
    client = authenticated_client
    customer = _client_a(client)
    agent = _agent_a(client, customer["id"])
    conversation = _conversation_a(client, agent["id"])
    _as_b(client)
    assert client.get(f"/api/clients/{customer['id']}").status_code == 404
    assert client.patch(f"/api/clients/{customer['id']}", json={"name": "robado"}).status_code == 404
    assert client.delete(f"/api/clients/{customer['id']}").status_code == 404
    assert client.get(f"/api/agents/{agent['id']}").status_code == 404
    assert client.patch(f"/api/agents/{agent['id']}", json={"name": "robado"}).status_code == 404
    assert client.delete(f"/api/agents/{agent['id']}").status_code == 404
    assert client.get(f"/api/conversations/{conversation['id']}").status_code == 404
    assert client.post(f"/api/conversations/{conversation['id']}/read").status_code == 404
    assert client.delete(f"/api/conversations/{conversation['id']}").status_code == 404


def test_channel_configuration_is_invisible_across_agencies(authenticated_client):
    client = authenticated_client
    customer = _client_a(client)
    agent = _agent_a(client, customer["id"])
    configured = client.put(f"/api/webchat/channels/{customer['id']}", json={
        "agent_id": agent["id"], "is_enabled": True, "greeting": "Hola", "color": "#075985", "position": "right",
    })
    assert configured.status_code == 200, configured.text
    _as_b(client)
    assert client.get(f"/api/webchat/channels/{customer['id']}").status_code == 404
    # A complete payload: validation must pass so the answer comes from the
    # ownership boundary, not from the request shape.
    assert client.put(f"/api/webchat/channels/{customer['id']}", json={
        "agent_id": agent["id"], "is_enabled": True, "greeting": "Hola", "color": "#075985", "position": "right",
    }).status_code == 404


def test_teams_calendar_and_pipeline_are_invisible_across_agencies(authenticated_client):
    client = authenticated_client
    customer = _client_a(client)
    team = client.post(f"/api/clients/{customer['id']}/teams", json={"name": "Equipo A"})
    assert team.status_code == 201, team.text
    member = client.post(f"/api/clients/{customer['id']}/calendar/members", json={"name": "Dra. A", "role": "Vet"})
    assert member.status_code == 201, member.text
    stage = client.post(f"/api/clients/{customer['id']}/pipeline/stages", json={"name": "Nuevo", "color": "#2f6df0"})
    assert stage.status_code == 201, stage.text
    _as_b(client)
    assert client.get(f"/api/clients/{customer['id']}/teams").status_code == 404
    assert client.get(f"/api/clients/{customer['id']}/calendar").status_code == 404
    assert client.get(f"/api/clients/{customer['id']}/pipeline/board").status_code == 404
    assert client.get(f"/api/clients/{customer['id']}/pipeline/stages").status_code == 404


def test_an_api_token_is_confined_to_its_agency(authenticated_client):
    client = authenticated_client
    customer_a = _client_a(client)
    _as_b(client)
    customer_b = client.post("/api/clients", json={"name": "Cliente B", "is_active": True}).json()
    integration = client.post("/api/integrations", json={"name": "B reader", "preset": "read_only"})
    assert integration.status_code == 201, integration.text
    issued = client.post(f"/api/integrations/{integration.json()['id']}/tokens", json={"expires_in_days": 30})
    assert issued.status_code == 201, issued.text
    headers = {"Authorization": f"Bearer {issued.json()['token']}"}
    assert client.get(f"/api/clients/{customer_a['id']}", headers=headers).status_code == 404
    assert client.get(f"/api/clients/{customer_b['id']}", headers=headers).status_code == 200
    _as_a(client)
    assert client.get(f"/api/clients/{customer_a['id']}").status_code == 200


def test_remaining_client_resources_are_invisible_across_agencies(authenticated_client):
    client = authenticated_client
    customer = _client_a(client)
    _as_b(client)
    assert client.get(f"/api/clients/by-slug/{customer['portal_slug']}").status_code == 404
    assert client.get(f"/api/clients/{customer['id']}/portal-users").status_code == 404
    assert client.get(f"/api/clients/{customer['id']}/contact-tags").status_code == 404
    assert client.get(f"/api/clients/{customer['id']}/templates").status_code == 404
    assert client.get(f"/api/clients/{customer['id']}/export").status_code == 404


def test_reports_never_cross_agencies(authenticated_client):
    import json
    import uuid
    from datetime import date

    from app.models import Client, UsageRecord
    from conftest import TestingSession

    client = authenticated_client
    customer = _client_a(client)
    with TestingSession() as db:
        owner = db.get(Client, uuid.UUID(customer["id"]))
        db.add(UsageRecord(
            agency_id=owner.agency_id, provider="openrouter", model="openai/gpt-5.6-luna",
            input_tokens=10, output_tokens=5,
        ))
        db.commit()
    today = date.today().isoformat()
    _as_b(client)
    theirs = client.get("/api/reports/costs", params={"from": today, "to": today})
    assert theirs.status_code == 200, theirs.text
    assert "gpt-5.6-luna" not in json.dumps(theirs.json())
    _as_a(client)
    assert "gpt-5.6-luna" in json.dumps(client.get("/api/reports/costs", params={"from": today, "to": today}).json())
