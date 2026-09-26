"""The copy of a client offered before deleting it."""

import io
import json
import zipfile

from conftest import customer_conversation


def test_the_export_holds_the_clients_data_and_no_secret(authenticated_client):
    client = authenticated_client
    customer = client.post("/api/clients", json={"name": "Acme Dental", "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post("/api/agents", json={
        "client_id": customer["id"], "provider": "openrouter", "model": "openai/gpt-5.6-luna",
        "name": "Vera", "instructions": "", "personality": "", "is_active": True,
    }).json()
    conversation = customer_conversation(client, agent["id"])
    client.post(f"/api/clients/{customer['id']}/resources", json={"kind": "link", "name": "Book", "url": "https://x.test"})
    client.post(f"/api/clients/{customer['id']}/portal-users",
                json={"name": "Ana", "email": "ana@acme.test", "password": "secure-portal"})

    response = client.get(f"/api/clients/{customer['id']}/export")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "openlivery-acme-dental-" in response.headers["content-disposition"]

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(archive.namelist())
    assert {"client.jsonl", "conversations.jsonl", "messages.jsonl", "resources.jsonl", "README.json"} <= names
    readme = json.loads(archive.read("README.json"))
    assert readme["counts"]["conversations"] >= 1 and readme["counts"]["resources"] == 1

    everything = b"".join(archive.read(name) for name in names).decode("utf-8")
    assert conversation["id"] in everything
    # Portal users are not exported at all, and no password hash or secret column leaks anywhere.
    assert "password_hash" not in everything and "ana@acme.test" not in everything
    client_row = json.loads(archive.read("client.jsonl").decode("utf-8").splitlines()[0])
    assert not any(key.startswith("encrypted") or key == "logo_data" for key in client_row)


def test_the_export_is_closed_to_api_tokens(authenticated_client):
    client = authenticated_client
    customer = client.post("/api/clients", json={"name": "Acme", "is_active": True}).json()
    integration = client.post("/api/integrations", json={"name": "Full", "preset": "full"}).json()
    token = client.post(f"/api/integrations/{integration['id']}/tokens", json={"expires_in_days": 30}).json()["token"]
    refused = client.get(f"/api/clients/{customer['id']}/export", headers={"Authorization": f"Bearer {token}"})
    assert refused.status_code == 403
