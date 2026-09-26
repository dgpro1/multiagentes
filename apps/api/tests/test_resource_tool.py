"""The declarative enviar_recurso tool, through the playground route and the
real tool loop, with a scripted provider and an in-memory bucket."""

import json
from unittest.mock import AsyncMock

from app.services.tools import loop
from app.services.tools.resource_tool import declared_resources
from test_resources import bucket, connect, upload  # noqa: F401 - the bucket fixture

PROMPT = """Eres la recepcionista.
Cuando pidan precios usa [Herramienta: enviar_recurso] con:
[Recurso: Catalog]
[Recurso: Book online]
[Recurso: Missing one]
"""


def test_declared_resources_parsing():
    text = "[Recurso: Catálogo 2026] [recurso:  Promo ] [Recurso: catalogo 2026]\n[Recurso: ]"
    assert declared_resources(text) == ["Catálogo 2026", "Promo"]
    assert declared_resources(None) == []


def _setup(client):
    customer = client.post("/api/clients", json={"name": "Acme", "is_active": True}).json()
    connect(client, customer["id"])
    assert upload(client, customer["id"]).status_code == 201  # "Catalog", a PDF
    link = client.post(f"/api/clients/{customer['id']}/resources", json={
        "kind": "link", "name": "Book online", "url": "https://acme.test/book",
        "message_template": "Hola {{contact.name}}, reserva aquí: {{url}}",
    })
    assert link.status_code == 201, link.text
    # Active but not cited by the prompt: the model must never see it.
    client.post(f"/api/clients/{customer['id']}/resources", json={
        "kind": "link", "name": "Internal", "url": "https://acme.test/internal"})
    assert client.put("/api/providers/openrouter", json={"api_key": "test-key"}).status_code == 200
    agent = client.post("/api/agents", json={
        "client_id": customer["id"], "name": "Vera", "provider": "openrouter", "model": "gpt-5",
        "is_active": True, "instructions": PROMPT,
    }).json()
    return customer, agent


def _call(*names):
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": "enviar_recurso",
                                                          "arguments": json.dumps({"resource": name})}}
        for i, name in enumerate(names)
    ]}}]}


def test_the_agent_sends_a_file_and_a_link(authenticated_client, bucket, monkeypatch):  # noqa: F811
    client = authenticated_client
    customer, agent = _setup(client)
    provider = AsyncMock(side_effect=[
        _call("Catalog", "Book online"),
        {"choices": [{"message": {"role": "assistant", "content": "Te comparto nuestro catálogo."}}]},
    ])
    monkeypatch.setattr(loop, "_post_json", provider)
    conversation = client.post("/api/conversations", json={"agent_id": agent["id"]}).json()
    response = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "¿Precios?"})
    assert response.status_code == 200, response.text

    tool = provider.await_args_list[0].args[2]["tools"]
    spec = next(t["function"] for t in tool if t["function"]["name"] == "enviar_recurso")
    # Only the cited, existing resources are offered.
    assert spec["parameters"]["properties"]["resource"]["enum"] == ["Catalog", "Book online"]
    assert "Internal" not in spec["description"]

    messages = response.json()["messages"]
    reply = next(m for m in messages if m["role"] == "assistant" and m["content"].startswith("Te comparto"))
    # The link arrives verbatim, from the template, after the model's sentence.
    assert reply["content"].endswith("reserva aquí: https://acme.test/book")
    files = [m for m in messages if m["role"] == "assistant" and m.get("attachments")]
    assert len(files) == 1
    assert files[0]["attachments"][0]["filename"] == "catalog.pdf"


def test_an_unknown_resource_is_a_tool_error_and_nothing_is_sent(authenticated_client, bucket, monkeypatch):  # noqa: F811
    client = authenticated_client
    customer, agent = _setup(client)
    provider = AsyncMock(side_effect=[
        _call("Internal"),
        {"choices": [{"message": {"role": "assistant", "content": "No tengo ese recurso."}}]},
    ])
    monkeypatch.setattr(loop, "_post_json", provider)
    conversation = client.post("/api/conversations", json={"agent_id": agent["id"]}).json()
    response = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "Hola"})
    assert response.status_code == 200, response.text
    returned = provider.await_args_list[1].args[2]["messages"][-1]
    assert returned["role"] == "tool" and "Recurso desconocido" in returned["content"]
    messages = response.json()["messages"]
    assert not [m for m in messages if m.get("attachments")]
    assert "https://acme.test/internal" not in json.dumps(messages)


def test_a_file_that_cannot_be_read_leaves_a_note_and_the_reply_goes_out(authenticated_client, bucket, monkeypatch):  # noqa: F811
    client = authenticated_client
    customer, agent = _setup(client)
    bucket.objects.clear()  # the customer emptied the bucket behind our back
    provider = AsyncMock(side_effect=[
        _call("Catalog"),
        {"choices": [{"message": {"role": "assistant", "content": "Aquí va el catálogo."}}]},
    ])
    monkeypatch.setattr(loop, "_post_json", provider)
    conversation = client.post(f"/api/conversations", json={"agent_id": agent["id"]}).json()
    response = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "Catálogo"})
    assert response.status_code == 200, response.text
    messages = response.json()["messages"]
    assert any(m["content"] == "Aquí va el catálogo." for m in messages)
    assert not [m for m in messages if m.get("attachments")]


def test_without_a_bucket_only_links_are_offered(authenticated_client, bucket, monkeypatch):  # noqa: F811
    client = authenticated_client
    customer, agent = _setup(client)
    client.delete(f"/api/clients/{customer['id']}/storage")
    provider = AsyncMock(side_effect=[{"choices": [{"message": {"role": "assistant", "content": "Hola"}}]}])
    monkeypatch.setattr(loop, "_post_json", provider)
    conversation = client.post("/api/conversations", json={"agent_id": agent["id"]}).json()
    client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "Hola"})
    tools = provider.await_args_list[0].args[2]["tools"]
    spec = next(t["function"] for t in tools if t["function"]["name"] == "enviar_recurso")
    assert spec["parameters"]["properties"]["resource"]["enum"] == ["Book online"]
