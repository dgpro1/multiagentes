"""A client's daily AI spending cap: it switches the AI off when reached, and a person can resume it."""

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.routers import widget as widget_router
from app.services import ai as ai_service
from test_conversation_number import _customer


def _widget(client: TestClient, customer: dict, agent: dict) -> str:
    channel = client.put(f"/api/webchat/channels/{customer['id']}", json={"agent_id": agent["id"], "is_enabled": True})
    assert channel.status_code == 200, channel.text
    return channel.json()["public_id"]


def _say(client: TestClient, public_id: str, session: str, text: str = "hola") -> dict:
    sent = client.post(f"/api/widget/{public_id}/messages", json={"session_id": session, "content": text})
    assert sent.status_code == 200, sent.text
    return sent.json()


def test_a_client_without_a_cap_is_never_paused(authenticated_client, monkeypatch):
    client = authenticated_client
    customer, agent = _customer(client, "Free Co")
    public_id = _widget(client, customer, agent)
    answer = AsyncMock(return_value=ai_service.Completion(text="Hola", input_tokens=10, output_tokens=5, cost_usd=500.0))
    monkeypatch.setattr(widget_router, "run_completion", answer)
    _say(client, public_id, "s1")
    _say(client, public_id, "s1", "otra vez")
    assert answer.await_count == 2
    shown = client.get(f"/api/clients/{customer['id']}").json()
    assert (shown["ai_daily_cap_usd"], shown["ai_paused"]) == (None, False)


def test_reaching_the_cap_switches_the_ai_off_until_it_is_resumed(authenticated_client, monkeypatch):
    client = authenticated_client
    customer, agent = _customer(client, "Capped Co")
    public_id = _widget(client, customer, agent)
    answer = AsyncMock(return_value=ai_service.Completion(text="Hola", input_tokens=10, output_tokens=5, cost_usd=0.6))
    monkeypatch.setattr(widget_router, "run_completion", answer)

    updated = client.patch(f"/api/clients/{customer['id']}", json={"ai_daily_cap_usd": 1.0})
    assert updated.status_code == 200, updated.text
    assert (updated.json()["ai_daily_cap_usd"], updated.json()["ai_paused"]) == (1.0, False)

    _say(client, public_id, "s1")
    assert client.get(f"/api/clients/{customer['id']}").json()["ai_paused"] is False  # 0.60 of 1.00
    _say(client, public_id, "s1", "y mas")
    assert client.get(f"/api/clients/{customer['id']}").json()["ai_paused"] is True  # 1.20 reached it

    spent = answer.await_count
    _say(client, public_id, "s1", "sigues ahi?")
    _say(client, public_id, "s2", "hola de nuevo")
    assert answer.await_count == spent  # nothing more is spent while it is off

    resumed = client.patch(f"/api/clients/{customer['id']}", json={"resume_ai": True})
    assert resumed.json()["ai_paused"] is False
    _say(client, public_id, "s3", "estas?")
    assert answer.await_count == spent + 1


def test_the_cap_can_be_removed_and_must_be_positive(authenticated_client):
    client = authenticated_client
    customer, _ = _customer(client, "Tidy Co")
    assert client.patch(f"/api/clients/{customer['id']}", json={"ai_daily_cap_usd": 0}).status_code == 422
    assert client.patch(f"/api/clients/{customer['id']}", json={"ai_daily_cap_usd": 5}).json()["ai_daily_cap_usd"] == 5
    assert client.patch(f"/api/clients/{customer['id']}", json={"ai_daily_cap_usd": None}).json()["ai_daily_cap_usd"] is None
