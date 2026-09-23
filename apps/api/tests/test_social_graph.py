from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services import messaging_provider as provider_client
from app.services import social_graph as graph


def channel(provider="instagram", **extra):
    values = dict(provider=provider, external_account_id="acct-1", status="connected", is_enabled=True,
                  human_agent_enabled=True, provider_profile_id="prof-1")
    values.update(extra)
    return SimpleNamespace(**values)


def conversation(thread_id="thread-1", chat_id="person-1"):
    return SimpleNamespace(provider_conversation_id=thread_id, external_chat_id=chat_id)


def test_unknown_providers_are_rejected():
    with pytest.raises(HTTPException):
        graph.provider_name("tiktok")
    assert graph.provider_name("instagram") == "instagram"


def test_disconnected_channels_cannot_send(monkeypatch):
    monkeypatch.setattr(provider_client, "send_message", AsyncMock())
    with pytest.raises(HTTPException) as error:
        import asyncio

        asyncio.run(graph.send_text(channel(status="error"), conversation(), "Hello"))
    assert error.value.status_code == 409
    provider_client.send_message.assert_not_awaited()


def test_instagram_text_limit_counts_utf8_bytes_and_never_truncates(monkeypatch):
    send = AsyncMock(return_value={"messageId": "mid.1"})
    monkeypatch.setattr(provider_client, "send_message", send)
    import asyncio

    assert asyncio.run(graph.send_text(channel(), conversation(), "😀" * 250)) == "mid.1"
    with pytest.raises(HTTPException) as error:
        asyncio.run(graph.send_text(channel(), conversation(), "😀" * 251))
    assert error.value.status_code == 400
    assert send.await_count == 1
    assert send.call_args.kwargs["message"] == "😀" * 250


def test_human_tag_is_attached_only_when_the_window_needs_it(monkeypatch):
    import asyncio

    send = AsyncMock(return_value={"messageId": "mid.1"})
    monkeypatch.setattr(provider_client, "send_message", send)
    asyncio.run(graph.send_text(channel("messenger"), conversation(), "Hello", human_agent=True))
    assert send.call_args.kwargs["messaging_type"] == "MESSAGE_TAG"
    assert send.call_args.kwargs["message_tag"] == "HUMAN_AGENT"
    asyncio.run(graph.send_text(channel("messenger"), conversation(), "Hello"))
    assert "messaging_type" not in send.call_args.kwargs


def test_human_agent_tag_requires_an_enabled_connection(monkeypatch):
    import asyncio

    monkeypatch.setattr(provider_client, "send_message", AsyncMock(return_value={"messageId": "mid.1"}))
    with pytest.raises(HTTPException) as error:
        asyncio.run(graph.send_text(channel(human_agent_enabled=False), conversation(), "Hello", human_agent=True))
    assert error.value.status_code == 409


def test_media_needs_a_public_https_url(monkeypatch):
    import asyncio

    monkeypatch.setattr(provider_client, "send_message", AsyncMock(return_value={"messageId": "mid.1"}))
    with pytest.raises(HTTPException):
        asyncio.run(graph.send_media(channel(), conversation(), "image", "http://internal/file.png"))
    with pytest.raises(HTTPException):
        asyncio.run(graph.send_media(channel(), conversation(), "sticker", "https://cdn.example/s.png"))


def test_missing_thread_is_resolved_from_the_participant(monkeypatch):
    import asyncio

    send = AsyncMock(return_value={"messageId": "mid.1"})
    monkeypatch.setattr(provider_client, "send_message", send)
    monkeypatch.setattr(provider_client, "list_conversations",
                        AsyncMock(return_value=([{"id": "thread-9", "participantId": "person-1"}], None)))
    bare = conversation(thread_id=None)
    assert asyncio.run(graph.send_text(channel(), bare, "Hello")) == "mid.1"
    assert bare.provider_conversation_id == "thread-9"
    assert send.call_args.args[1] == "thread-9"


def test_verify_account_reads_the_linked_account(monkeypatch):
    import asyncio

    monkeypatch.setattr(provider_client, "require_account", AsyncMock(return_value={
        "account_id": "acct-1", "display_name": "Shop", "username": "shop"}))
    profile = asyncio.run(graph.verify_account("instagram", "acct-1"))
    assert profile["id"] == "acct-1" and profile["name"] == "Shop"


def test_sender_profile_matches_the_participant(monkeypatch):
    import asyncio

    monkeypatch.setattr(provider_client, "list_conversations", AsyncMock(return_value=(
        [{"participantId": "person-1", "participantName": "Ana", "participantUsername": "ana"}], None)))
    assert asyncio.run(graph.sender_profile(channel(), "person-1")) == {"name": "Ana", "username": "ana"}
    assert asyncio.run(graph.sender_profile(channel(), "someone-else")) == {}


def test_mark_read_never_fails_the_caller(monkeypatch):
    import asyncio

    from fastapi import HTTPException as HTTPError

    monkeypatch.setattr(provider_client, "mark_read", AsyncMock(side_effect=HTTPError(502, "down")))
    asyncio.run(graph.mark_read(channel(), conversation()))
    asyncio.run(graph.mark_read(channel(), conversation(thread_id=None)))


def test_reactions_go_through_the_thread(monkeypatch):
    import asyncio

    reacted = AsyncMock(return_value=None)
    removed = AsyncMock(return_value=None)
    monkeypatch.setattr(provider_client, "send_reaction", reacted)
    monkeypatch.setattr(provider_client, "remove_reaction", removed)
    asyncio.run(graph.send_reaction(channel(), conversation(), "mid-1", "👍"))
    assert reacted.call_args.args == ("acct-1", "thread-1", "mid-1", "👍")
    asyncio.run(graph.send_reaction(channel(), conversation(), "mid-1", ""))
    removed.assert_awaited_once()
