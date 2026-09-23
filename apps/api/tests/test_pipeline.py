"""Sales pipeline: per-client stages, the kanban board, moving a conversation
by hand or through the agent's own move_pipeline_stage tool."""

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.config import get_settings
from app.services import ai as ai_service
from app.services import evolution as evolution_driver
from app.services import whatsapp_inbound as whatsapp_inbound_service
from conftest import customer_conversation


def _setup(client: TestClient, company: str):
    customer = client.post("/api/clients", json={"name": company, "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post(
        "/api/agents",
        json={"client_id": customer["id"], "provider": "openrouter", "model": "gpt-4.1-mini", "name": "Vera", "instructions": "", "personality": "", "is_active": True},
    ).json()
    return customer, agent


def _stage(client: TestClient, customer_id: str, name: str, color: str | None = None) -> dict:
    payload = {"name": name}
    if color:
        payload["color"] = color
    created = client.post(f"/api/clients/{customer_id}/pipeline/stages", json=payload)
    assert created.status_code == 201, created.text
    return created.json()


def test_stage_crud_and_reorder(authenticated_client: TestClient):
    client = authenticated_client
    customer, _agent = _setup(client, "Pipeline Co")
    base = f"/api/clients/{customer['id']}/pipeline/stages"

    nuevo = _stage(client, customer["id"], "Nuevo", "#2f6df0")
    interesado = _stage(client, customer["id"], "Interesado")
    ganado = _stage(client, customer["id"], "Ganado")
    assert [s["name"] for s in client.get(base).json()] == ["Nuevo", "Interesado", "Ganado"]

    # Duplicate names are refused.
    assert client.post(base, json={"name": "Nuevo"}).status_code == 409

    renamed = client.patch(f"{base}/{interesado['id']}", json={"name": "Negociando", "color": "#00a67d"}).json()
    assert renamed["name"] == "Negociando" and renamed["color"] == "#00a67d"

    reordered = client.post(f"{base}/reorder", json={"stage_ids": [ganado["id"], nuevo["id"], interesado["id"]]}).json()
    assert [s["name"] for s in reordered] == ["Ganado", "Nuevo", "Negociando"]

    # Reorder must name every stage exactly once.
    assert client.post(f"{base}/reorder", json={"stage_ids": [ganado["id"], nuevo["id"]]}).status_code == 422

    assert client.delete(f"{base}/{ganado['id']}").status_code == 204
    assert [s["name"] for s in client.get(base).json()] == ["Nuevo", "Negociando"]


def test_moving_a_conversation_and_the_board(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent = _setup(client, "Board Co")
    nuevo = _stage(client, customer["id"], "Nuevo")
    ganado = _stage(client, customer["id"], "Ganado")
    conversation = customer_conversation(client, agent["id"])

    moved = client.patch(f"/api/conversations/{conversation['id']}/pipeline", json={"pipeline_stage_id": nuevo["id"], "deal_value": 250.5})
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["pipeline_stage_id"] == nuevo["id"] and body["pipeline_stage_name"] == "Nuevo"
    assert body["deal_value"] == 250.5
    events = [m["activity"]["event"] for m in body["messages"] if m.get("kind") == "activity" and m.get("activity")]
    assert "pipeline_stage_changed" in events

    board = client.get(f"/api/clients/{customer['id']}/pipeline/board").json()
    by_name = {s["name"]: s for s in board["stages"]}
    assert by_name["Nuevo"]["conversation_count"] == 1
    assert by_name["Nuevo"]["deal_value_total"] == 250.5
    assert len(board["cards"]) == 1 and board["cards"][0]["id"] == conversation["id"]

    # Moving again keeps the value when the caller resends it, and dropping
    # the stage takes it off the board without touching the conversation.
    client.patch(f"/api/conversations/{conversation['id']}/pipeline", json={"pipeline_stage_id": ganado["id"], "deal_value": 250.5})
    off_board = client.patch(f"/api/conversations/{conversation['id']}/pipeline", json={"pipeline_stage_id": None, "deal_value": None})
    assert off_board.json()["pipeline_stage_id"] is None and off_board.json()["deal_value"] is None
    board_after = client.get(f"/api/clients/{customer['id']}/pipeline/board").json()
    # The card stays on the board, now in the virtual "not in the pipeline" column.
    assert [c["pipeline_stage_id"] for c in board_after["cards"]] == [None]
    assert board_after["unassigned_count"] == 1


def test_deleting_a_stage_clears_it_from_conversations(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent = _setup(client, "Cleanup Co")
    stage = _stage(client, customer["id"], "Nuevo")
    conversation = customer_conversation(client, agent["id"])
    client.patch(f"/api/conversations/{conversation['id']}/pipeline", json={"pipeline_stage_id": stage["id"], "deal_value": 10})

    assert client.delete(f"/api/clients/{customer['id']}/pipeline/stages/{stage['id']}").status_code == 204
    current = client.get(f"/api/conversations/{conversation['id']}").json()
    assert current["pipeline_stage_id"] is None
    assert current["deal_value"] == 10, "dropping the stage clears the stage, not the deal value"


def test_portal_view_is_free_but_managing_needs_the_permission(authenticated_client: TestClient):
    client = authenticated_client
    customer, _agent = _setup(client, "Portal Pipeline Co")
    slug = customer["portal_slug"]
    client.post(
        f"/api/clients/{customer['id']}/portal-users",
        json={"name": "Ana", "email": f"ana@{slug}.com", "password": "secure-portal", "role": "agent"},
    )
    client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_enabled": True})

    portal = TestClient(client.app)
    portal.post(f"/api/portal/{slug}/login", json={"email": f"ana@{slug}.com", "password": "secure-portal"})
    assert portal.get(f"/api/portal/{slug}/pipeline/board").status_code == 200
    denied = portal.post(f"/api/portal/{slug}/pipeline/stages", json={"name": "Nuevo"})
    assert denied.status_code == 403

    client.post(
        f"/api/clients/{customer['id']}/portal-users",
        json={"name": "Owner", "email": f"owner@{slug}.com", "password": "secure-portal", "role": "admin"},
    )
    admin = TestClient(client.app)
    admin.post(f"/api/portal/{slug}/login", json={"email": f"owner@{slug}.com", "password": "secure-portal"})
    created = admin.post(f"/api/portal/{slug}/pipeline/stages", json={"name": "Nuevo"})
    assert created.status_code == 201, created.text


def _inbound(client: TestClient, channel_id: str, text: str, message_id: str = "wa-in-1"):
    return client.post(
        f"/api/internal/whatsapp/channels/{channel_id}/inbound",
        headers={"X-Bridge-Token": get_settings().whatsapp_bridge_token},
        json={"external_message_id": message_id, "remote_jid": "573001112233@s.whatsapp.net", "sender_name": "Sam", "text": text},
    )


def _moving_completion(target_stage_name: str, reply: str, captured: dict | None = None):
    async def fake(db, agent, base_url, api_key, messages, temperature=None, max_tokens=None, extra_specs=None):
        if captured is not None:
            captured["system"] = messages[0]["content"]
        pipeline_spec = next((spec for spec in (extra_specs or []) if spec.name == "move_pipeline_stage"), None)
        if pipeline_spec:
            result, is_error = pipeline_spec.handler({"stage": target_stage_name})
            assert not is_error, result
        return ai_service.Completion(text=reply, input_tokens=1, output_tokens=1)

    return fake


def test_agent_moves_the_deal_through_its_own_tool(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    customer, agent = _setup(client, "Agentic Pipeline Co")
    _stage(client, customer["id"], "Nuevo")
    interesado = _stage(client, customer["id"], "Interesado")
    channel = client.put(f"/api/whatsapp/channels/{customer['id']}", json={"agent_id": agent["id"]}).json()

    monkeypatch.setattr(evolution_driver, "send_text", AsyncMock(return_value="wa-generated"))
    monkeypatch.setattr(evolution_driver, "mark_read", AsyncMock())
    captured: dict = {}
    monkeypatch.setattr(
        whatsapp_inbound_service, "run_completion",
        _moving_completion("Interesado", "Perfecto, te cuento los precios.", captured),
    )
    response = _inbound(client, channel["id"], "sí me interesa, cuánto cuesta?")
    assert response.status_code == 200, response.text
    conversation = client.get(f"/api/conversations/{response.json()['conversation_id']}").json()
    assert conversation["pipeline_stage_id"] == interesado["id"]
    assert "PIPELINE DE VENTAS" in captured["system"] and "Interesado" in captured["system"]


def test_no_stages_means_the_tool_is_not_offered(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    customer, agent = _setup(client, "No Pipeline Co")
    channel = client.put(f"/api/whatsapp/channels/{customer['id']}", json={"agent_id": agent["id"]}).json()

    monkeypatch.setattr(evolution_driver, "send_text", AsyncMock(return_value="wa-generated"))
    monkeypatch.setattr(evolution_driver, "mark_read", AsyncMock())

    async def fake(db, agent, base_url, api_key, messages, temperature=None, max_tokens=None, extra_specs=None):
        assert not any(spec.name == "move_pipeline_stage" for spec in (extra_specs or []))
        return ai_service.Completion(text="Hola!", input_tokens=1, output_tokens=1)

    monkeypatch.setattr(whatsapp_inbound_service, "run_completion", fake)
    response = _inbound(client, channel["id"], "hola")
    assert response.status_code == 200, response.text


def test_quick_lead_creates_contact_and_case_in_stage(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent = _setup(client, "Quick Lead Co")
    nuevo = _stage(client, customer["id"], "Nuevo")
    created = client.post(f"/api/clients/{customer['id']}/pipeline/leads", json={
        "agent_id": agent["id"], "contact_name": "Rita", "contact_phone": "573009998877",
        "pipeline_stage_id": nuevo["id"], "deal_value": 1200,
    })
    assert created.status_code == 201, created.text
    card = created.json()
    assert card["pipeline_stage_id"] == nuevo["id"] and card["deal_value"] == 1200
    assert card["contact_id"] is not None and card["channel"] == "manual"

    detail = client.get(f"/api/conversations/{card['id']}").json()
    assert detail["mode"] == "human" and detail["status"] == "open"
    assert detail["contact_id"] == card["contact_id"]
    events = [m["activity"]["event"] for m in detail["messages"] if m.get("kind") == "activity" and m.get("activity")]
    assert "started" in events

    board = client.get(f"/api/clients/{customer['id']}/pipeline/board").json()
    assert [c["id"] for c in board["cards"]] == [card["id"]]
    assert board["stages"][0]["conversation_count"] == 1


def test_quick_lead_validation(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent = _setup(client, "Quick Lead Validation Co")
    other, other_agent = _setup(client, "Other Co")
    nuevo = _stage(client, customer["id"], "Nuevo")
    foreign_stage = _stage(client, other["id"], "Ajeno")
    base = f"/api/clients/{customer['id']}/pipeline/leads"
    good = {"agent_id": agent["id"], "contact_name": "Sam"}

    # An agent or a stage from another client is refused.
    assert client.post(base, json={**good, "agent_id": other_agent["id"]}).status_code == 400
    assert client.post(base, json={**good, "pipeline_stage_id": foreign_stage["id"]}).status_code == 404
    # A phone too short is not a phone number.
    assert client.post(base, json={**good, "contact_phone": "123"}).status_code == 422

    # Without phone and stage the lead lands unassigned.
    created = client.post(base, json=good)
    assert created.status_code == 201, created.text
    assert created.json()["pipeline_stage_id"] is None
    board = client.get(f"/api/clients/{customer['id']}/pipeline/board").json()
    assert board["unassigned_count"] == 1

    # Without an agent the client's first active agent answers for it.
    defaulted = client.post(base, json={"contact_name": "Noa"})
    assert defaulted.status_code == 201, defaulted.text
    assert defaulted.json()["contact_name"] == "Noa"


def test_board_cards_carry_contact_tags(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent = _setup(client, "Tagged Lead Co")
    slug = customer["portal_slug"]
    client.post(
        f"/api/clients/{customer['id']}/portal-users",
        json={"name": "Owner", "email": f"owner@{slug}.com", "password": "secure-portal", "role": "admin"},
    )
    client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_enabled": True})
    _stage(client, customer["id"], "Nuevo")
    lead = client.post(f"/api/clients/{customer['id']}/pipeline/leads", json={
        "agent_id": agent["id"], "contact_name": "Vera", "contact_phone": "573001112233",
    }).json()
    vip = client.post(f"/api/clients/{customer['id']}/contact-tags", json={"name": "VIP", "color": "#EF4444"}).json()
    portal = TestClient(client.app)
    portal.post(f"/api/portal/{slug}/login", json={"email": f"owner@{slug}.com", "password": "secure-portal"})
    assert portal.put(f"/api/portal/{slug}/contacts/{lead['contact_id']}/tags",
                      json={"tag_ids": [vip["id"]]}).status_code == 200

    card = client.get(f"/api/clients/{customer['id']}/pipeline/board").json()["cards"][0]
    assert card["contact_id"] == lead["contact_id"]
    assert card["tags"] == [{"name": "VIP", "color": "#ef4444"}]


def test_portal_quick_lead_is_free_for_agents(authenticated_client: TestClient):
    from conftest import login_legacy_owner

    client = authenticated_client
    customer, agent = _setup(client, "Portal Lead Co")
    slug = customer["portal_slug"]
    client.post(
        f"/api/clients/{customer['id']}/portal-users",
        json={"name": "Ana", "email": f"ana@{slug}.com", "password": "secure-portal", "role": "agent"},
    )
    client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_enabled": True})
    nuevo = _stage(client, customer["id"], "Nuevo")

    portal = TestClient(client.app)
    portal.post(f"/api/portal/{slug}/login", json={"email": f"ana@{slug}.com", "password": "secure-portal"})
    created = portal.post(f"/api/portal/{slug}/pipeline/leads", json={
        "agent_id": agent["id"], "contact_name": "Luis", "pipeline_stage_id": nuevo["id"], "deal_value": 300,
    })
    assert created.status_code == 201, created.text
    assert created.json()["pipeline_stage_id"] == nuevo["id"]
    board = portal.get(f"/api/portal/{slug}/pipeline/board").json()
    assert [c["id"] for c in board["cards"]] == [created.json()["id"]]

    login_legacy_owner(client)
    assert client.get(f"/api/clients/{customer['id']}/pipeline/board").status_code == 404
