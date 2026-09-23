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


def test_idempotent_reply_sends_once_and_marks_the_replay(authenticated_client: TestClient):
    from sqlalchemy import select

    from app.models import Message

    from conftest import TestingSession

    client = authenticated_client
    customer, agent, token = _setup(client)
    headers = {**_auth(token), "Idempotency-Key": "reply-1"}
    conversation = customer_conversation(client, agent["id"])
    assert client.patch(f"/api/conversations/{conversation['id']}/mode", json={"mode": "human"}).status_code == 200
    url = f"/api/v1/clients/{customer['id']}/conversations/{conversation['id']}/reply"

    first = client.post(url, headers=headers, json={"content": "Hi, Ana"})
    assert first.status_code == 200, first.text
    assert "api_replay" not in first.json()
    second = client.post(url, headers=headers, json={"content": "Hi, Ana"})
    assert second.status_code == 200
    assert second.json()["api_replay"] is True
    with TestingSession() as db:
        assert db.scalars(select(Message).where(Message.role == "assistant")).all().__len__() == 1

    # The same key with another body is refused, and keys never cross tokens.
    assert client.post(url, headers=headers, json={"content": "Other"}).status_code == 422
    other = _auth(_token(client, ["inbox.reply"]))
    assert "api_replay" not in client.post(url, headers={**other, "Idempotency-Key": "reply-1"},
                                           json={"content": "Hi, Ana"}).json()


def test_idempotent_contact_create(authenticated_client: TestClient):
    from sqlalchemy import select

    from app.models import Contact

    from conftest import TestingSession

    client = authenticated_client
    customer, _, token = _setup(client)
    headers = {**_auth(token), "Idempotency-Key": "contact-1"}
    url = f"/api/v1/clients/{customer['id']}/contacts"
    payload = {"name": "Ana", "phone": "573001112233"}

    first = client.post(url, headers=headers, json=payload)
    assert first.status_code == 201, first.text
    second = client.post(url, headers=headers, json=payload)
    assert second.status_code == 201
    assert second.json()["api_replay"] is True
    assert second.json()["id"] == first.json()["id"]
    with TestingSession() as db:
        assert db.scalars(select(Contact)).all().__len__() == 1


def _setup6(client: TestClient) -> tuple[dict, dict, str]:
    customer = client.post("/api/clients", json={"name": "Acme", "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post(
        "/api/agents",
        json={"client_id": customer["id"], "provider": "openrouter", "model": "gpt-4.1-mini",
              "name": "Vera", "instructions": "", "personality": "", "is_active": True},
    ).json()
    token = _token(client, ["agents.read", "agents.write", "agents.knowledge", "channels.read",
                            "inbox.read", "inbox.reply", "reports.read", "calendar.read", "calendar.manage"])
    return customer, agent, token


def test_agents_crud_and_prompt(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent, token = _setup6(client)
    headers = _auth(token)
    base = f"/api/v1/clients/{customer['id']}/agents"

    listed = client.get(base, headers=headers)
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()["data"]] == [agent["id"]]
    assert listed.json()["data"][0]["_links"]["self"].endswith(f"/agents/{agent['id']}")

    one = client.get(f"{base}/{agent['id']}", headers=headers)
    assert one.status_code == 200 and one.json()["name"] == "Vera"

    # The path owns the client: a body naming another one is refused.
    assert client.post(base, headers=headers, json={
        "client_id": "00000000-0000-0000-0000-000000000000", "name": "Nope"}).status_code == 409
    created = client.post(base, headers=headers, json={
        "client_id": customer["id"], "provider": "openrouter", "model": "gpt-4.1-mini", "name": "Api"})
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "Api"

    patched = client.patch(f"{base}/{created.json()['id']}", headers=headers, json={"temperature": 0.2})
    assert patched.status_code == 200 and patched.json()["temperature"] == 0.2
    assert client.patch(f"{base}/{created.json()['id']}", headers=headers,
                        json={"reply_delay_min_seconds": 50, "reply_delay_max_seconds": 5}).status_code == 422
    assert client.patch(f"{base}/{created.json()['id']}", headers=headers,
                        json={"client_id": "00000000-0000-0000-0000-000000000000"}).status_code == 409

    prompt = client.get(f"{base}/{agent['id']}/prompt", headers=headers)
    assert prompt.status_code == 200 and "Vera" in prompt.json()["prompt"]

    # Another client's path never names this agent.
    other = client.post("/api/clients", json={"name": "Other", "is_active": True}).json()
    assert client.get(f"/api/v1/clients/{other['id']}/agents/{agent['id']}", headers=headers).status_code == 404

    assert client.delete(f"{base}/{created.json()['id']}", headers=headers).status_code == 204
    assert client.get(f"/api/agents/{created.json()['id']}").status_code == 404


def test_knowledge_documents_and_qa(authenticated_client: TestClient, monkeypatch):
    from app.routers import api_v1 as api_v1_router

    class _FakePage:
        def extract_text(self):
            return "Hello knowledge"

    class _Reader:
        def __init__(self, _data):
            self.pages = [_FakePage()]

    monkeypatch.setattr(api_v1_router, "PdfReader", _Reader)

    client = authenticated_client
    customer, agent, token = _setup6(client)
    headers = _auth(token)
    base = f"/api/v1/clients/{customer['id']}/agents/{agent['id']}"

    assert client.get(f"{base}/documents", headers=headers).json() == {
        "data": [], "total": 0, "_links": {"self": f"/api/v1/clients/{customer['id']}/agents/{agent['id']}/documents"}}
    assert client.post(f"{base}/documents", headers=headers,
                       files={"file": ("notes.txt", b"hello", "text/plain")}).status_code == 400

    uploaded = client.post(f"{base}/documents", headers=headers,
                           files={"file": ("notes.pdf", b"%PDF-test", "application/pdf")})
    assert uploaded.status_code == 201, uploaded.text
    document = uploaded.json()
    assert document["filename"] == "notes.pdf" and document["status"] == "processed"
    assert document["character_count"] > 0
    assert document["_links"]["self"].endswith(f"/documents/{document['id']}")

    assert client.delete(f"{base}/documents/{document['id']}", headers=headers).status_code == 204
    assert client.delete(f"{base}/documents/{document['id']}", headers=headers).status_code == 404

    qa_headers = {**headers, "Idempotency-Key": "qa-1"}
    first = client.post(f"{base}/qa", headers=qa_headers, json={"question": "Hours?", "answer": "Nine to five."})
    assert first.status_code == 201, first.text
    second = client.post(f"{base}/qa", headers=qa_headers, json={"question": "Hours?", "answer": "Nine to five."})
    assert second.json()["api_replay"] is True and second.json()["id"] == first.json()["id"]

    pairs = client.get(f"{base}/qa", headers=headers).json()
    assert [row["id"] for row in pairs["data"]] == [first.json()["id"]]
    assert client.delete(f"{base}/qa/{first.json()['id']}", headers=headers).status_code == 204


def test_channels_list(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent, token = _setup6(client)
    channel = client.put(f"/api/whatsapp/channels/{customer['id']}", json={"agent_id": agent["id"]}).json()

    response = client.get(f"/api/v1/clients/{customer['id']}/channels", headers=_auth(token))
    assert response.status_code == 200, response.text
    rows = {row["id"]: row for row in response.json()["data"]}
    assert rows[channel["id"]]["type"] == "whatsapp_qr"
    assert set(rows[channel["id"]]) >= {"id", "client_id", "type", "label", "status", "connected",
                                        "agent_id", "last_error", "last_connected_at", "_links"}
    assert rows[channel["id"]]["agent_id"] == agent["id"]


def test_messages_list(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent, token = _setup6(client)
    headers = _auth(token)
    conversation = customer_conversation(client, agent["id"])
    assert client.patch(f"/api/conversations/{conversation['id']}/mode", json={"mode": "human"}).status_code == 200
    assert client.post(f"/api/v1/clients/{customer['id']}/conversations/{conversation['id']}/reply",
                       headers=headers, json={"content": "Hi, Ana"}).status_code == 200

    response = client.get(f"/api/v1/clients/{customer['id']}/conversations/{conversation['id']}/messages", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] >= 1
    assert body["data"][-1]["content"] == "Hi, Ana"
    assert body["data"][-1]["kind"] == "message"
    assert body["data"][-1]["_links"]["self"].endswith(f"/messages/{body['data'][-1]['id']}")
    assert {row["kind"] for row in body["data"]} <= {"message", "activity"}


def test_reports_costs_replies_operations(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent, token = _setup6(client)
    headers = _auth(token)
    params = {"from": "2026-01-01", "to": "2026-02-01"}

    costs = client.get(f"/api/v1/clients/{customer['id']}/reports/costs", headers=headers, params=params)
    assert costs.status_code == 200, costs.text
    assert set(costs.json()["totals"]) >= {"cost_usd", "replies", "conversations", "input_tokens", "output_tokens"}
    assert costs.json()["by_agent"] == [] and costs.json()["_links"]["self"].endswith("/reports/costs")

    replies = client.get(f"/api/v1/clients/{customer['id']}/reports/replies", headers=headers, params=params)
    assert replies.status_code == 200 and replies.json()["total"] == 0

    operations = client.get(f"/api/v1/clients/{customer['id']}/reports/operations", headers=headers, params=params)
    assert operations.status_code == 200, operations.text
    assert operations.json()["_links"]["self"].endswith("/reports/operations")

    # An agent of another client cannot scope this client's numbers.
    other = client.post("/api/clients", json={"name": "Other", "is_active": True}).json()
    foreign = client.post("/api/agents", json={"client_id": other["id"], "provider": "openrouter",
                                               "model": "gpt-4.1-mini", "name": "Away", "is_active": True}).json()
    assert client.get(f"/api/v1/clients/{customer['id']}/reports/costs", headers=headers,
                      params={**params, "agent_id": foreign["id"]}).status_code == 400


def test_calendar_overview_and_members(authenticated_client: TestClient):
    client = authenticated_client
    customer, _, token = _setup6(client)
    headers = _auth(token)
    base = f"/api/v1/clients/{customer['id']}/calendar"

    overview = client.get(base, headers=headers)
    assert overview.status_code == 200, overview.text
    assert overview.json()["members"] == []

    member_headers = {**headers, "Idempotency-Key": "cal-1"}
    first = client.post(f"{base}/members", headers=member_headers, json={"name": "Ventas", "role": "sales"})
    assert first.status_code == 201, first.text
    second = client.post(f"{base}/members", headers=member_headers, json={"name": "Ventas", "role": "sales"})
    assert second.json()["api_replay"] is True and second.json()["id"] == first.json()["id"]

    assert client.get(base, headers=headers).json()["members"].__len__() == 1
    assert client.delete(f"{base}/members/{first.json()['id']}", headers=headers).status_code == 204
    assert client.get(base, headers=headers).json()["members"] == []


def _setup7(client: TestClient) -> tuple[dict, dict, str]:
    customer = client.post("/api/clients", json={"name": "Acme", "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post(
        "/api/agents",
        json={"client_id": customer["id"], "provider": "openrouter", "model": "gpt-4.1-mini",
              "name": "Vera", "instructions": "", "personality": "", "is_active": True},
    ).json()
    token = _token(client, ["clients.read", "clients.write", "inbox.read", "inbox.manage",
                            "pipeline.read", "pipeline.manage", "tags.read", "tags.manage"])
    return customer, agent, token


def test_clients_create_and_update(authenticated_client: TestClient):
    client = authenticated_client
    customer, _, token = _setup7(client)
    headers = {**_auth(token), "Idempotency-Key": "client-1"}

    created = client.post("/api/v1/clients", headers=headers, json={"name": "Beta"})
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "Beta"
    assert created.json()["_links"]["self"].endswith(f"/api/v1/clients/{created.json()['id']}")
    replayed = client.post("/api/v1/clients", headers=headers, json={"name": "Beta"})
    assert replayed.json()["api_replay"] is True and replayed.json()["id"] == created.json()["id"]

    updated = client.patch(f"/api/v1/clients/{customer['id']}", headers=_auth(token), json={"name": "Acme II"})
    assert updated.status_code == 200 and updated.json()["name"] == "Acme II"


def test_conversation_takeover_and_resolve(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent, token = _setup7(client)
    headers = _auth(token)
    conversation = customer_conversation(client, agent["id"])
    base = f"/api/v1/clients/{customer['id']}/conversations/{conversation['id']}"

    taken = client.patch(f"{base}/mode", headers=headers, json={"mode": "human"})
    assert taken.status_code == 200 and taken.json()["mode"] == "human"
    back = client.patch(f"{base}/mode", headers=headers, json={"mode": "ai"})
    assert back.json()["mode"] == "ai"

    resolved = client.patch(f"{base}/status", headers=headers, json={"status": "resolved"})
    assert resolved.status_code == 200 and resolved.json()["status"] == "resolved"
    assert resolved.json()["_links"]["self"].endswith(f"/conversations/{conversation['id']}/status")


def test_pipeline_stages_and_leads(authenticated_client: TestClient):
    client = authenticated_client
    customer, _, token = _setup7(client)
    headers = _auth(token)
    base = f"/api/v1/clients/{customer['id']}/pipeline"

    assert client.get(f"{base}/stages", headers=headers).json()["data"] == []
    first = client.post(f"{base}/stages", headers={**headers, "Idempotency-Key": "stage-1"},
                        json={"name": "Nuevo", "color": "#3b82f6"})
    assert first.status_code == 201, first.text
    assert first.json()["_links"]["self"].endswith(f"/stages/{first.json()['id']}")
    replayed = client.post(f"{base}/stages", headers={**headers, "Idempotency-Key": "stage-1"},
                           json={"name": "Nuevo", "color": "#3b82f6"})
    assert replayed.json()["api_replay"] is True
    second = client.post(f"{base}/stages", headers=headers, json={"name": "Ganado"}).json()

    renamed = client.patch(f"{base}/stages/{first.json()['id']}", headers=headers, json={"name": "Contactado"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Contactado"

    reordered = client.post(f"{base}/stages/reorder", headers=headers,
                            json={"stage_ids": [second["id"], first.json()["id"]]})
    assert [row["id"] for row in reordered.json()["data"]] == [second["id"], first.json()["id"]]

    lead = client.post(f"{base}/leads", headers=headers,
                       json={"contact_name": "Lead", "contact_phone": "573001112233", "pipeline_stage_id": second["id"]})
    assert lead.status_code == 201, lead.text

    assert client.delete(f"{base}/stages/{first.json()['id']}", headers=headers).status_code == 204


def test_tags_crud(authenticated_client: TestClient):
    client = authenticated_client
    customer, _, token = _setup7(client)
    headers = _auth(token)
    base = f"/api/v1/clients/{customer['id']}/tags"

    assert client.get(base, headers=headers).json()["data"] == []
    created = client.post(base, headers={**headers, "Idempotency-Key": "tag-1"},
                          json={"name": "VIP", "color": "#3b82f6"})
    assert created.status_code == 201, created.text
    assert created.json()["_links"]["self"].endswith(f"/tags/{created.json()['id']}")
    replayed = client.post(base, headers={**headers, "Idempotency-Key": "tag-1"},
                           json={"name": "VIP", "color": "#3b82f6"})
    assert replayed.json()["api_replay"] is True

    renamed = client.patch(f"{base}/{created.json()['id']}", headers=headers, json={"name": "VVIP"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "VVIP"
    assert client.patch(f"{base}/{created.json()['id']}", headers=headers,
                        json={"name": "VVIP", "route_team_id": "00000000-0000-0000-0000-000000000000"}).status_code == 200

    assert client.delete(f"{base}/{created.json()['id']}", headers=headers).status_code == 204
    assert client.get(base, headers=headers).json()["data"] == []
