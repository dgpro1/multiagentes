"""Agent management from the client portal.

With the ``agents`` function on, a client's portal admins get the agency's own
agent screens under ``/api/portal/{slug}/manage``: the very same handlers,
confined to that one client. These tests pin the door (feature, role, session),
the confinement (another client, another agency, the agency's keys) and that the
panel routes are untouched.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.deps import get_current_user
from app.models import Agency, Agent, Client, PortalUser
from app.portal_features import FEATURE_DISABLED_DETAIL
from app.portal_permissions import AGENTS_MANAGE, has_permission, permissions_for
from app.routers import agent_tools as agent_tools_router
from app.routers import conversations as conversations_router
from app.routers import portal_manage
from app.security import hash_password
from app.services import ai as ai_service
from conftest import TestingSession, customer_conversation

PASSWORD = "secure-portal"
SECRET = "sk-or-very-secret-key-0123456789"
FORBIDDEN = {"detail": "Your role cannot do this"}

# Every (method, path) the portal mounts, relative to /api/portal/{slug}/manage.
EXPECTED_ROUTES = {
    ("GET", "/agents"), ("POST", "/agents"),
    ("GET", "/agents/{agent_id}"), ("PATCH", "/agents/{agent_id}"), ("DELETE", "/agents/{agent_id}"),
    ("GET", "/agents/{agent_id}/prompt"),
    ("GET", "/agents/{agent_id}/documents"), ("POST", "/agents/{agent_id}/documents"),
    ("POST", "/agents/{agent_id}/documents/reindex"),
    ("POST", "/agents/{agent_id}/documents/{document_id}/reindex"),
    ("DELETE", "/agents/{agent_id}/documents/{document_id}"),
    ("GET", "/agents/{agent_id}/qa"), ("POST", "/agents/{agent_id}/qa"),
    ("DELETE", "/agents/{agent_id}/qa/{qa_id}"),
    ("GET", "/agents/{agent_id}/escalation-rules"), ("PUT", "/agents/{agent_id}/escalation-rules"),
    ("GET", "/agents/{agent_id}/tools"), ("POST", "/agents/{agent_id}/tools"),
    ("PATCH", "/agents/{agent_id}/tools/{tool_id}"), ("DELETE", "/agents/{agent_id}/tools/{tool_id}"),
    ("POST", "/agents/{agent_id}/tools/test-mcp"),
    ("GET", "/clients"),
    ("GET", "/clients/{client_id}/teams"), ("GET", "/clients/{client_id}/portal-users"),
    ("GET", "/clients/{client_id}/contact-tags"), ("PATCH", "/clients/{client_id}/contact-tags/{tag_id}"),
    ("GET", "/conversations"), ("POST", "/conversations"),
    ("GET", "/conversations/{conversation_id}"), ("DELETE", "/conversations/{conversation_id}"),
    ("POST", "/conversations/{conversation_id}/messages"), ("POST", "/conversations/{conversation_id}/media"),
    ("GET", "/conversations/{conversation_id}/attachments/{attachment_id}"),
    ("GET", "/catalog/available"), ("GET", "/catalog/models"), ("GET", "/catalog/embedding-models"),
    ("GET", "/catalog/models/{model_id:path}"),
    ("GET", "/providers"),
}

AGENT_PAYLOAD = {"provider": "openrouter", "model": "gpt-4.1-mini", "name": "Vera", "instructions": "Be kind.", "is_active": True}


def _person(client: TestClient, customer: dict, email: str, role: str) -> None:
    created = client.post(
        f"/api/clients/{customer['id']}/portal-users",
        json={"name": email.split("@")[0].title(), "email": email, "password": PASSWORD, "role": role},
    )
    assert created.status_code == 201, created.text


def _sign_in(client: TestClient, customer: dict, email: str) -> None:
    response = client.post(f"/api/portal/{customer['portal_slug']}/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text


def _switch(client: TestClient, customer: dict, **features: bool) -> None:
    response = client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_features": features})
    assert response.status_code == 200, response.text


def _agent(client: TestClient, customer: dict, name: str = "Vera") -> dict:
    created = client.post("/api/agents", json={**AGENT_PAYLOAD, "client_id": customer["id"], "name": name})
    assert created.status_code == 201, created.text
    return created.json()


@pytest.fixture
def world(authenticated_client: TestClient) -> dict:
    """Two clients of one agency, an agent each, and portal people for the first.

    The agency is signed in on the test client; The admin is signed in
    to the portal too; ``_as_helper`` swaps the portal session for the agent's.
    """
    client = authenticated_client
    client.put("/api/providers/openrouter", json={"api_key": SECRET})
    mine = client.post("/api/clients", json={"name": "Mine Co", "is_active": True}).json()
    other = client.post("/api/clients", json={"name": "Other Co", "is_active": True}).json()
    _person(client, mine, "boss@mine.co", "admin")
    _person(client, mine, "helper@mine.co", "agent")
    assert client.patch(f"/api/clients/{mine['id']}/portal", json={"portal_enabled": True}).status_code == 200
    _switch(client, mine, agents=True)
    world = {
        "api": client,
        "mine": mine,
        "other": other,
        "agent": _agent(client, mine, "Mine agent"),
        "foreign": _agent(client, other, "Other agent"),
        "base": f"/api/portal/{mine['portal_slug']}/manage",
    }
    _sign_in(client, mine, "boss@mine.co")
    return world


def _as_helper(world: dict) -> None:
    _sign_in(world["api"], world["mine"], "helper@mine.co")


def _concrete(path: str) -> str:
    """A mounted path with every parameter filled by a random id."""
    filled = path
    for name in ("agent_id", "document_id", "qa_id", "tool_id", "client_id", "tag_id", "conversation_id", "attachment_id"):
        filled = filled.replace("{" + name + "}", str(uuid.uuid4()))
    return filled.replace("{model_id:path}", "openai/gpt-4.1")


# The door -------------------------------------------------------------------


def test_only_admins_hold_the_permission():
    assert has_permission("admin", AGENTS_MANAGE)
    assert not has_permission("agent", AGENTS_MANAGE)
    assert AGENTS_MANAGE in permissions_for("admin") and AGENTS_MANAGE not in permissions_for("agent")


# The channel screens are mounted beside the agent ones (tests/test_portal_channels.py).
CHANNEL_PREFIXES = ("/whatsapp", "/webchat", "/social")


def test_the_mounted_surface_is_exactly_the_agent_screens_and_none_takes_a_panel_user():
    """The surface grows on purpose, and every route of it asks for a portal
    actor, never for the agency's own session."""
    mounted = set()
    for route in portal_manage.router.routes:
        mounted |= {(method, route.path.removeprefix("/portal/{slug}/manage")) for method in route.methods - {"HEAD", "OPTIONS"}}
        seen, stack = [], [route.dependant]
        while stack:
            node = stack.pop()
            seen.append(node.call)
            stack.extend(node.dependencies)
        assert portal_manage.ACTORS & set(seen), route.path
        assert get_current_user not in seen, route.path
    agent_side = {(m, p) for m, p in mounted if not p.startswith(CHANNEL_PREFIXES) and (m, p) != ("GET", "/clients/{client_id}")}
    assert agent_side == EXPECTED_ROUTES


def test_without_a_session_every_mounted_route_is_a_401(world):
    client = world["api"]
    client.cookies.delete("portal_access_token")
    for method, path in sorted(EXPECTED_ROUTES):
        response = client.request(method, world["base"] + _concrete(path))
        assert response.status_code == 401, f"{method} {path}: {response.status_code}"


def test_with_the_function_off_every_mounted_route_is_refused(world):
    client = world["api"]
    _switch(client, world["mine"], agents=False)
    for method, path in sorted(EXPECTED_ROUTES):
        response = client.request(method, world["base"] + _concrete(path))
        assert response.status_code == 403, f"{method} {path}: {response.status_code}"
        assert response.json() == {"detail": FEATURE_DISABLED_DETAIL}, f"{method} {path}"


def test_switching_it_off_cuts_access_at_the_next_request(world):
    client, base = world["api"], world["base"]
    assert client.get(f"{base}/agents").status_code == 200
    _switch(client, world["mine"], agents=False)
    assert client.get(f"{base}/agents").status_code == 403
    _switch(client, world["mine"], agents=True)
    assert client.get(f"{base}/agents").status_code == 200


def test_the_agent_role_is_refused_everywhere(world):
    _as_helper(world)
    client = world["api"]
    for method, path in sorted(EXPECTED_ROUTES):
        response = client.request(method, world["base"] + _concrete(path))
        assert response.status_code == 403, f"{method} {path}: {response.status_code}"
        assert response.json() == FORBIDDEN, f"{method} {path}"


# What an admin does ---------------------------------------------------------


def test_an_admin_runs_an_agent_end_to_end_with_the_panels_shapes(world):
    client, base, mine = world["api"], world["base"], world["mine"]

    listed = client.get(f"{base}/agents")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [world["agent"]["id"]]
    assert listed.json() == [item for item in client.get("/api/agents").json() if item["client_id"] == mine["id"]]

    created = client.post(f"{base}/agents", json={**AGENT_PAYLOAD, "client_id": mine["id"], "name": "Beto"})
    assert created.status_code == 201, created.text
    agent_id = created.json()["id"]
    assert created.json()["client_id"] == mine["id"]
    panel = client.get(f"/api/agents/{agent_id}")
    assert set(created.json()) == set(panel.json())  # timestamps render per session, the keys are the panel's
    assert client.get(f"{base}/agents/{agent_id}").json() == panel.json()

    patched = client.patch(f"{base}/agents/{agent_id}", json={"instructions": "Answer briefly.", "temperature": 0.2, "model": "openai/gpt-4.1"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["instructions"] == "Answer briefly." and patched.json()["temperature"] == 0.2
    assert set(patched.json()) == set(client.get(f"/api/agents/{agent_id}").json())
    assert client.patch(f"{base}/agents/{agent_id}", json={"reply_delay_min_seconds": 9, "reply_delay_max_seconds": 1}).status_code == 422

    prompt = client.get(f"{base}/agents/{agent_id}/prompt")
    assert prompt.status_code == 200 and "Answer briefly." in prompt.json()["prompt"]
    assert prompt.json() == client.get(f"/api/agents/{agent_id}/prompt").json()

    # Knowledge: a Q&A pair and a document (unreadable on purpose, it lands as an error row).
    pair = client.post(f"{base}/agents/{agent_id}/qa", json={"question": "Hours?", "answer": "9 to 5"})
    assert pair.status_code == 201 and set(pair.json()) == {"id", "question", "answer"}
    assert client.get(f"{base}/agents/{agent_id}/qa").json() == client.get(f"/api/agents/{agent_id}/qa").json()
    document = client.post(f"{base}/agents/{agent_id}/documents", files={"file": ("menu.pdf", b"not a pdf", "application/pdf")})
    assert document.status_code == 201, document.text
    assert set(document.json()) == set(client.get(f"/api/agents/{agent_id}/documents").json()[0])
    assert client.get(f"{base}/agents/{agent_id}/documents").json() == client.get(f"/api/agents/{agent_id}/documents").json()
    assert client.post(f"{base}/agents/{agent_id}/documents", files={"file": ("a.txt", b"x", "text/plain")}).status_code == 400
    assert client.delete(f"{base}/agents/{agent_id}/documents/{document.json()['id']}").status_code == 204
    assert client.delete(f"{base}/agents/{agent_id}/qa/{pair.json()['id']}").status_code == 204
    assert client.get(f"{base}/agents/{agent_id}/qa").json() == []

    # Tools.
    tool_body = {
        "type": "http", "name": "check_order", "description": "Look up an order",
        "url": "https://api.example.test/orders/{order_id}", "http_method": "GET",
        "prompt_instructions": "Use when the customer asks about an order.", "headers": {"Authorization": "Bearer sk-hidden"},
    }
    tool = client.post(f"{base}/agents/{agent_id}/tools", json=tool_body)
    assert tool.status_code == 201, tool.text
    assert tool.json()["has_headers"] is True and "sk-hidden" not in tool.text
    tool_id = tool.json()["id"]
    assert client.get(f"{base}/agents/{agent_id}/tools").json() == client.get(f"/api/agents/{agent_id}/tools").json()
    toggled = client.patch(f"{base}/agents/{agent_id}/tools/{tool_id}", json={"enabled": False})
    assert toggled.status_code == 200 and toggled.json()["enabled"] is False
    assert client.delete(f"{base}/agents/{agent_id}/tools/{tool_id}").status_code == 204

    # Escalation rules: teams and people of the client, read from the client routes.
    team = client.post(f"/api/clients/{mine['id']}/teams", json={"name": "Sales"}).json()
    teams = client.get(f"{base}/clients/{mine['id']}/teams")
    assert teams.status_code == 200 and [row["id"] for row in teams.json()] == [team["id"]]
    people = client.get(f"{base}/clients/{mine['id']}/portal-users")
    assert people.status_code == 200 and {row["email"] for row in people.json()} == {"boss@mine.co", "helper@mine.co"}
    config = {"default_team_id": team["id"], "default_assignee_id": None, "builtin_enabled": True,
              "rules": [{"condition": "Asks for a refund", "team_id": team["id"], "assignee_id": None, "is_active": True}]}
    saved = client.put(f"{base}/agents/{agent_id}/escalation-rules", json=config)
    assert saved.status_code == 200, saved.text
    assert saved.json() == client.get(f"/api/agents/{agent_id}/escalation-rules").json()
    assert client.get(f"{base}/agents/{agent_id}/escalation-rules").json() == saved.json()

    # Delete keeps the panel's semantics (a tombstone that vanishes from lists).
    assert client.delete(f"{base}/agents/{agent_id}").status_code == 204
    assert client.get(f"{base}/agents/{agent_id}").status_code == 404
    assert client.get(f"/api/agents/{agent_id}").status_code == 404
    assert agent_id not in [item["id"] for item in client.get(f"{base}/agents").json()]


def test_the_clients_route_answers_with_its_own_client_only(world):
    client, base = world["api"], world["base"]
    rows = client.get(f"{base}/clients")
    assert rows.status_code == 200
    assert [row["id"] for row in rows.json()] == [world["mine"]["id"]]
    assert len(client.get("/api/clients").json()) == 2  # the panel still sees both


def test_the_playground_rehearses_with_the_clients_agent_only(world, monkeypatch):
    client, base = world["api"], world["base"]
    monkeypatch.setattr(conversations_router, "run_completion", AsyncMock(return_value=ai_service.Completion(text="Hola desde la prueba")))

    made = client.post(f"{base}/conversations", json={"agent_id": world["agent"]["id"]})
    assert made.status_code == 201, made.text
    assert made.json()["channel"] == "playground"
    thread = made.json()["id"]
    reply = client.post(f"{base}/conversations/{thread}/messages", json={"content": "Hola"})
    assert reply.status_code == 200, reply.text
    assert [m["content"] for m in reply.json()["messages"]] == ["Hola", "Hola desde la prueba"]
    assert client.get(f"{base}/conversations", params={"agent_id": world["agent"]["id"]}).json()[0]["id"] == thread
    assert client.get(f"{base}/conversations/{thread}").status_code == 200

    # Another client's agent cannot be rehearsed with; its rehearsal is not reachable.
    assert client.post(f"{base}/conversations", json={"agent_id": world["foreign"]["id"]}).status_code == 400
    foreign_thread = client.post("/api/conversations", json={"agent_id": world["foreign"]["id"]}).json()["id"]
    assert client.get(f"{base}/conversations/{foreign_thread}").status_code == 404
    assert client.post(f"{base}/conversations/{foreign_thread}/messages", json={"content": "hi"}).status_code == 404
    assert client.delete(f"{base}/conversations/{foreign_thread}").status_code == 404
    assert foreign_thread not in [row["id"] for row in client.get(f"{base}/conversations").json()]

    # The client's own customer threads are the inbox's, not the playground's.
    real = customer_conversation(client, world["agent"]["id"])
    assert client.get(f"{base}/conversations/{real['id']}").status_code == 404
    assert client.post(f"{base}/conversations/{real['id']}/messages", json={"content": "hi"}).status_code == 404
    assert client.delete(f"{base}/conversations/{real['id']}").status_code == 404
    assert real["id"] not in [row["id"] for row in client.get(f"{base}/conversations").json()]

    assert client.delete(f"{base}/conversations/{thread}").status_code == 204
    assert client.get(f"{base}/conversations/{thread}").status_code == 404


def test_an_mcp_server_inside_the_network_is_refused_for_the_portal_only(world, monkeypatch):
    client, base, agent_id = world["api"], world["base"], world["agent"]["id"]
    discovered = [{"name": "lookup", "description": "Find", "input_schema": {"type": "object", "properties": {}}}]
    discover = AsyncMock(return_value=discovered)
    monkeypatch.setattr(agent_tools_router, "discover_mcp_tools", discover)
    body = {"url": "http://127.0.0.1:9/mcp", "transport": "streamable_http"}

    refused = client.post(f"{base}/agents/{agent_id}/tools/test-mcp", json=body)
    assert refused.status_code == 422 and "private" in refused.json()["detail"]
    created = client.post(
        f"{base}/agents/{agent_id}/tools",
        json={"type": "mcp", "name": "crm", "description": "CRM", "url": body["url"], "transport": body["transport"]},
    )
    assert created.status_code == 422
    discover.assert_not_awaited()
    assert client.get(f"/api/agents/{agent_id}/tools").json() == []

    # The agency chooses its own servers and is not held to that rule.
    assert client.post(f"/api/agents/{agent_id}/tools/test-mcp", json=body).status_code == 200
    # A public address passes for the portal too.
    monkeypatch.setattr(agent_tools_router, "_blocked_reason", lambda url: None)
    assert client.post(f"{base}/agents/{agent_id}/tools/test-mcp", json={**body, "url": "https://mcp.example.test/mcp"}).status_code == 200


def test_routing_a_tag_needs_the_tags_function_too(world):
    client, base, mine = world["api"], world["base"], world["mine"]
    tag = client.post(f"/api/clients/{mine['id']}/contact-tags", json={"name": "VIP", "color": "#2f6df0"}).json()
    team = client.post(f"/api/clients/{mine['id']}/teams", json={"name": "Sales"}).json()
    listed = client.get(f"{base}/clients/{mine['id']}/contact-tags")
    assert listed.status_code == 200 and [row["id"] for row in listed.json()] == [tag["id"]]
    routed = client.patch(f"{base}/clients/{mine['id']}/contact-tags/{tag['id']}", json={"route_team_id": team["id"]})
    assert routed.status_code == 200 and routed.json()["route_team_id"] == team["id"]
    _switch(client, mine, tags=False)
    refused = client.patch(f"{base}/clients/{mine['id']}/contact-tags/{tag['id']}", json={"route_team_id": None})
    assert refused.status_code == 403 and refused.json() == {"detail": FEATURE_DISABLED_DETAIL}


# Confinement ----------------------------------------------------------------


def test_another_clients_agent_is_a_404_on_every_route(world):
    client, base, foreign = world["api"], world["base"], world["foreign"]["id"]
    panel_tool = client.post(
        f"/api/agents/{foreign}/tools",
        json={"type": "http", "name": "secret_tool", "description": "x", "url": "https://api.example.test/x", "http_method": "GET"},
    ).json()
    pair = client.post(f"/api/agents/{foreign}/qa", json={"question": "q", "answer": "a"}).json()
    doc = client.post(f"/api/agents/{foreign}/documents", files={"file": ("x.pdf", b"nope", "application/pdf")}).json()
    root = f"{base}/agents/{foreign}"

    attempts = [
        ("GET", root, None),
        ("PATCH", root, {"name": "Hijacked"}),
        ("DELETE", root, None),
        ("GET", f"{root}/prompt", None),
        ("GET", f"{root}/documents", None),
        ("POST", f"{root}/documents", None),
        ("POST", f"{root}/documents/reindex", None),
        ("POST", f"{root}/documents/{doc['id']}/reindex", None),
        ("DELETE", f"{root}/documents/{doc['id']}", None),
        ("GET", f"{root}/qa", None),
        ("POST", f"{root}/qa", {"question": "q", "answer": "a"}),
        ("DELETE", f"{root}/qa/{pair['id']}", None),
        ("GET", f"{root}/escalation-rules", None),
        ("PUT", f"{root}/escalation-rules", {"default_team_id": None, "default_assignee_id": None, "builtin_enabled": True, "rules": []}),
        ("GET", f"{root}/tools", None),
        ("POST", f"{root}/tools", {"type": "http", "name": "mine", "description": "x", "url": "https://api.example.test/y", "http_method": "GET"}),
        ("PATCH", f"{root}/tools/{panel_tool['id']}", {"enabled": False}),
        ("DELETE", f"{root}/tools/{panel_tool['id']}", None),
        ("POST", f"{root}/tools/test-mcp", {"tool_id": panel_tool["id"], "url": "https://mcp.example.test/mcp"}),
    ]
    for method, path, body in attempts:
        kwargs = {"files": {"file": ("x.pdf", b"nope", "application/pdf")}} if (method, path) == ("POST", f"{root}/documents") else {"json": body}
        response = client.request(method, path, **kwargs)
        assert response.status_code == 404, f"{method} {path}: {response.status_code} {response.text}"

    # Nothing of the other client changed or left it.
    after = client.get(f"/api/agents/{foreign}").json()
    assert after["name"] == "Other agent" and after["is_active"] is True
    assert len(client.get(f"/api/agents/{foreign}/tools").json()) == 1
    assert len(client.get(f"/api/agents/{foreign}/qa").json()) == 1
    assert len(client.get(f"/api/agents/{foreign}/documents").json()) == 1
    assert foreign not in [item["id"] for item in client.get(f"{base}/agents").json()]


def test_a_tool_of_another_agent_is_not_reachable_through_your_own_agent(world):
    client, base = world["api"], world["base"]
    tool = client.post(
        f"/api/agents/{world['foreign']['id']}/tools",
        json={"type": "http", "name": "theirs", "description": "x", "url": "https://api.example.test/x", "http_method": "GET"},
    ).json()
    mine = f"{base}/agents/{world['agent']['id']}/tools/{tool['id']}"
    assert client.patch(mine, json={"enabled": False}).status_code == 404
    assert client.delete(mine).status_code == 404
    assert client.get(f"/api/agents/{world['foreign']['id']}/tools").json()[0]["enabled"] is True


def test_the_client_of_an_agent_cannot_be_changed_or_chosen_from_the_portal(world):
    client, base, other = world["api"], world["base"], world["other"]
    created = client.post(f"{base}/agents", json={**AGENT_PAYLOAD, "client_id": other["id"], "name": "Smuggled"})
    assert created.status_code == 404
    moved = client.patch(f"{base}/agents/{world['agent']['id']}", json={"client_id": other["id"]})
    assert moved.status_code == 404
    assert client.get(f"/api/agents/{world['agent']['id']}").json()["client_id"] == world["mine"]["id"]
    assert [a["name"] for a in client.get("/api/agents").json() if a["client_id"] == other["id"]] == ["Other agent"]
    # The client routes answer for the portal's own client only.
    for path in ("teams", "portal-users", "contact-tags"):
        assert client.get(f"{base}/clients/{other['id']}/{path}").status_code == 404
    assert client.patch(f"{base}/clients/{other['id']}/contact-tags/{uuid.uuid4()}", json={"route_team_id": None}).status_code == 404


def test_another_agency_and_its_sessions_are_shut_out(world):
    client, base = world["api"], world["base"]
    with TestingSession() as db:
        agency = Agency(name="Rival agency", slug="rival-agency")
        db.add(agency)
        db.flush()
        rival = Client(agency_id=agency.id, name="Rival Co", portal_slug="rival-co", portal_enabled=True,
                       portal_features={"agents": True})
        db.add(rival)
        db.flush()
        db.add(PortalUser(client_id=rival.id, name="Rita", email="rita@rival.co", role="admin", password_hash=hash_password(PASSWORD)))
        secret_agent = Agent(agency_id=agency.id, client_id=rival.id, name="Rival agent")
        db.add(secret_agent)
        db.commit()
        rival_agent_id = str(secret_agent.id)

    # Mine cannot see theirs...
    assert client.get(f"{base}/agents/{rival_agent_id}").status_code == 404
    assert client.get(f"{base}/agents/{rival_agent_id}/prompt").status_code == 404
    assert rival_agent_id not in [a["id"] for a in client.get(f"{base}/agents").json()]

    # ...and theirs cannot reach mine, nor use their session on my slug.
    assert client.post("/api/portal/rival-co/login", json={"email": "rita@rival.co", "password": PASSWORD}).status_code == 200
    assert client.get(f"{base}/agents").status_code == 401
    assert client.get(f"{base}/agents/{world['agent']['id']}").status_code == 401
    own = client.get("/api/portal/rival-co/manage/agents")
    assert own.status_code == 200 and [a["id"] for a in own.json()] == [rival_agent_id]
    assert client.get(f"/api/portal/rival-co/manage/agents/{world['agent']['id']}").status_code == 404
    assert client.get(f"/api/portal/rival-co/manage/agents/{world['foreign']['id']}").status_code == 404


def test_an_agency_session_alone_does_not_open_the_portal_routes(world):
    """The agency's cookie is not a portal session; the portal has its own door."""
    client = world["api"]
    client.cookies.delete("portal_access_token")
    assert client.get("/api/agents").status_code == 200
    assert client.get(f"{world['base']}/agents").status_code == 401


# Secrets --------------------------------------------------------------------


def test_the_catalogs_expose_no_key_or_connection_detail(world):
    client, base = world["api"], world["base"]

    panel = client.get("/api/providers").json()
    assert panel[0]["api_key_masked"] and panel[0]["source"] == "agency"

    portal = client.get(f"{base}/providers")
    assert portal.status_code == 200
    rows = portal.json()
    assert rows and all(set(row) == {"provider", "label", "configured", "source", "api_key_masked"} for row in rows)
    assert all(row["api_key_masked"] == "" for row in rows)
    assert [row["configured"] for row in rows] == [row["configured"] for row in panel]
    assert next(row for row in rows if row["provider"] == "openrouter")["configured"] is True
    assert SECRET not in portal.text and SECRET[:6] not in portal.text and SECRET[-4:] not in portal.text

    for path in ("/catalog/available", "/catalog/models", "/catalog/embedding-models", "/catalog/models/openai/gpt-4.1"):
        response = client.get(base + path)
        assert response.status_code == 200, f"{path}: {response.text}"
        assert response.json() == client.get("/api/" + path.lstrip("/")).json()
        assert SECRET not in response.text

    # Nothing that writes or tests a key is mounted.
    assert client.put(f"{base}/providers/openrouter", json={"api_key": "x"}).status_code in (404, 405)
    assert client.delete(f"{base}/providers/openrouter").status_code in (404, 405)
    assert client.post(f"{base}/providers/openrouter/test").status_code in (404, 405)
    assert client.get("/api/providers").json()[0]["configured"] is True


def test_the_panel_routes_keep_working_for_the_agency(world):
    client = world["api"]
    client.cookies.delete("portal_access_token")
    assert len(client.get("/api/agents").json()) == 2
    made = client.post("/api/agents", json={**AGENT_PAYLOAD, "client_id": world["other"]["id"], "name": "Another"})
    assert made.status_code == 201
    moved = client.patch(f"/api/agents/{made.json()['id']}", json={"client_id": world["mine"]["id"]})
    assert moved.status_code == 200 and moved.json()["client_id"] == world["mine"]["id"]
    assert len(client.get("/api/clients").json()) == 2
    thread = client.post("/api/conversations", json={"agent_id": world["foreign"]["id"]})
    assert thread.status_code == 201
    assert client.get(f"/api/conversations/{thread.json()['id']}").status_code == 200
    assert client.get("/api/catalog/models").status_code == 200
