"""The versioned public API: envelope, pagination, links and resources."""

from fastapi.testclient import TestClient

from fastapi.testclient import TestClient

from conftest import customer_conversation


def _token(client: TestClient, scopes: list[str]) -> str:
    integration = client.post("/api/integrations", json={"name": "n8n", "scopes": scopes}).json()
    issued = client.post(f"/api/integrations/{integration['id']}/tokens", json={"expires_in_days": 30}).json()
    return issued["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _setup(client: TestClient) -> tuple[dict, dict, str]:
    customer = client.post("/api/clients", json={"name": "Acme", "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post(
        "/api/agents",
        json={"client_id": customer["id"], "provider": "openrouter", "model": "gpt-4.1-mini",
              "name": "Vera", "instructions": "", "personality": "", "is_active": True},
    ).json()
    token = _token(client, ["clients.read", "contacts.read", "contacts.manage", "inbox.read",
                            "inbox.reply", "pipeline.read", "pipeline.manage", "calendar.read"])
    return customer, agent, token


def test_envelope_and_pagination(authenticated_client: TestClient):
    client = authenticated_client
    customer, _, token = _setup(client)
    headers = _auth(token)

    first = client.get("/api/v1/clients", headers=headers, params={"limit": 1, "page": 1})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["page"] == 1 and body["limit"] == 1 and body["total"] >= 1
    assert "page=1" in body["_links"]["self"] and "limit=1" in body["_links"]["self"]
    assert "next" in body["_links"] or body["total"] == 1

    # The limit is capped at 250, Kommo-style.
    capped = client.get("/api/v1/clients", headers=headers, params={"limit": 1000})
    assert capped.json()["limit"] == 250

    one = client.get(f"/api/v1/clients/{customer['id']}", headers=headers)
    assert one.status_code == 200
    assert one.json()["id"] == customer["id"]
    assert one.json()["_links"]["self"].endswith(f"/api/v1/clients/{customer['id']}")

    # Errors wear the envelope too, including unauthenticated ones.
    missing = client.get("/api/v1/clients/00000000-0000-0000-0000-000000000000", headers=headers)
    assert missing.status_code == 404
    assert missing.json() == {
        "title": "Not found",
        "type": "/api/v1/docs/errors#not-found",
        "status": 404,
        "detail": "Client not found",
    }
    # Without any credential at all the envelope still answers.
    bare = TestClient(client.app)
    assert bare.get("/api/v1/clients").status_code == 401
    assert bare.get("/api/v1/clients").json() == {
        "title": "Unauthorized",
        "type": "/api/v1/docs/errors#unauthorized",
        "status": 401,
        "detail": "You are not signed in",
    }


def test_validation_errors_name_fields(authenticated_client: TestClient):
    client = authenticated_client
    customer, _, token = _setup(client)
    response = client.post(f"/api/v1/clients/{customer['id']}/contacts", headers=_auth(token), json={"name": "x"})
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["title"] == "Validation failed" and body["status"] == 422
    assert body["type"] == "/api/v1/docs/errors#validation-failed"
    assert any(error["field"] == "phone" for error in body["validation-errors"])


def test_contacts_crud(authenticated_client: TestClient):
    client = authenticated_client
    customer, _, token = _setup(client)
    headers = _auth(token)

    created = client.post(f"/api/v1/clients/{customer['id']}/contacts", headers=headers,
                          json={"name": "Ana", "phone": "+57 300 111 2233"})
    assert created.status_code == 201, created.text
    contact = created.json()
    assert contact["phone"] == "573001112233"
    assert contact["_links"]["self"].endswith(f"/contacts/{contact['id']}")

    assert client.post(f"/api/v1/clients/{customer['id']}/contacts", headers=headers,
                       json={"name": "Again", "phone": "573001112233"}).status_code == 409

    listed = client.get(f"/api/v1/clients/{customer['id']}/contacts", headers=headers, params={"search": "ana"})
    assert listed.json()["total"] == 1

    updated = client.patch(f"/api/v1/clients/{customer['id']}/contacts/{contact['id']}", headers=headers,
                           json={"notes": "VIP"})
    assert updated.status_code == 200 and updated.json()["notes"] == "VIP"

    # A read-only token reads but never writes.
    reader = _auth(_token(client, ["contacts.read"]))
    assert client.get(f"/api/v1/clients/{customer['id']}/contacts", headers=reader).status_code == 200
    denied = client.post(f"/api/v1/clients/{customer['id']}/contacts", headers=reader,
                         json={"name": "No", "phone": "573009998877"})
    assert denied.status_code == 403
    assert denied.json()["detail"] == "This API token does not hold: contacts.manage"


def test_conversations_list_get_and_reply(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent, token = _setup(client)
    headers = _auth(token)
    conversation = customer_conversation(client, agent["id"])

    listed = client.get(f"/api/v1/clients/{customer['id']}/conversations", headers=headers)
    assert listed.status_code == 200
    assert [c["id"] for c in listed.json()["data"]] == [conversation["id"]]

    one = client.get(f"/api/v1/clients/{customer['id']}/conversations/{conversation['id']}", headers=headers)
    assert one.status_code == 200 and one.json()["_links"]["self"].endswith(conversation["id"])

    # The panel's own rule holds here too: take control before replying.
    assert client.post(f"/api/v1/clients/{customer['id']}/conversations/{conversation['id']}/reply",
                       headers=headers, json={"content": "Hi"}).status_code == 409
    assert client.patch(f"/api/conversations/{conversation['id']}/mode",
                        json={"mode": "human"}).status_code == 200
    replied = client.post(f"/api/v1/clients/{customer['id']}/conversations/{conversation['id']}/reply",
                          headers=headers, json={"content": "Hi, Ana"})
    assert replied.status_code == 200, replied.text
    assert replied.json()["mode"] == "human"


def test_pipeline_board_and_move(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent, token = _setup(client)
    headers = _auth(token)
    stage = client.post(f"/api/clients/{customer['id']}/pipeline/stages", json={"name": "Nuevo"}).json()
    conversation = customer_conversation(client, agent["id"])

    board = client.get(f"/api/v1/clients/{customer['id']}/pipeline/board", headers=headers)
    assert board.status_code == 200, board.text
    assert board.json()["_links"]["self"].endswith("/pipeline/board")
    assert board.json()["unassigned_count"] == 1

    moved = client.patch(f"/api/v1/clients/{customer['id']}/conversations/{conversation['id']}/pipeline",
                         headers=headers, json={"pipeline_stage_id": stage["id"], "deal_value": 100})
    assert moved.status_code == 200
    assert moved.json()["pipeline_stage_id"] == stage["id"]


def test_calendar_events_shape(authenticated_client: TestClient):
    client = authenticated_client
    customer, _, token = _setup(client)
    response = client.get(f"/api/v1/clients/{customer['id']}/calendar/events", headers=_auth(token),
                          params={"start": "2026-01-01T00:00:00+00:00", "end": "2026-01-08T00:00:00+00:00"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"] == [] and body["total"] == 0
    assert f"/api/v1/clients/{customer['id']}/calendar/events" in body["_links"]["self"]
