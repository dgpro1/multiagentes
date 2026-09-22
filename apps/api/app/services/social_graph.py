"""Instagram and Messenger delivery through the unified messaging provider.

Channels keep only the provider-side account id; the server key lives in
the environment. Threads are addressed by the provider conversation id
stored on each case. Errors surface as HTTPException with safe messages.
"""

import logging
from urllib.parse import urlsplit

from fastapi import HTTPException

from . import messaging_provider as provider

logger = logging.getLogger(__name__)

PROVIDERS = {"instagram", "messenger"}

# Provider platform value per channel provider.
PLATFORM = {"instagram": "instagram", "messenger": "facebook"}


def provider_name(provider: str) -> str:
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unsupported messaging provider")
    return provider


async def verify_account(provider_name_: str, account_id: str, profile_id: str | None = None) -> dict:
    """Confirm the account is connected and return its public profile."""
    provider_name(provider_name_)
    account = await provider.require_account(account_id, profile_id)
    return {
        "id": account["account_id"],
        "name": account.get("display_name") or account.get("username") or account_id,
        "username": account.get("username"),
        "scopes": [],
        "expires_at": None,
    }


async def sender_profile(channel, person: str) -> dict:
    """Name and handle of a person who wrote to the account. Webhooks carry
    the sender id; the profile is read back from the provider's thread list.
    A failed lookup leaves the message untouched."""
    try:
        items, _ = await provider.list_conversations(account_id=channel.external_account_id, limit=100)
    except HTTPException:
        return {}
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("participantId") or "") == person:
            return {
                "name": str(item.get("participantName") or "")[:180],
                "username": str(item.get("participantUsername") or "")[:180] or None,
            }
    return {}


def _thread(conversation) -> str:
    thread_id = conversation.provider_conversation_id or ""
    if not thread_id:
        raise HTTPException(status_code=409, detail="This conversation has no provider thread yet")
    return thread_id


def _delivery(channel, *, human: bool) -> tuple[str, bool]:
    """The provider account plus whether the send carries the approved tag
    for messaging outside the standard window. The window itself is checked
    by the caller through the shared reply policy; ``human`` arrives as that
    verdict (a real human reply needing the tag)."""
    if channel.status != "connected" or not channel.is_enabled or not channel.external_account_id:
        raise HTTPException(409, "Connect this messaging channel before replying")
    if human and not channel.human_agent_enabled:
        raise HTTPException(409, "Human Agent support has not been enabled for this connection")
    return channel.external_account_id, human


async def _resolve_thread(channel, conversation) -> str:
    if conversation.provider_conversation_id:
        return conversation.provider_conversation_id
    items, _ = await provider.list_conversations(account_id=channel.external_account_id, limit=100)
    for item in items:
        if isinstance(item, dict) and str(item.get("participantId") or "") == (conversation.external_chat_id or ""):
            thread_id = str(item.get("id") or "")
            if thread_id:
                conversation.provider_conversation_id = thread_id
                return thread_id
    raise HTTPException(status_code=409, detail="This conversation has no provider thread yet")


def _tag_kwargs(human_agent: bool, need_tag: bool) -> dict:
    if human_agent and need_tag:
        return {"messaging_type": "MESSAGE_TAG", "message_tag": "HUMAN_AGENT"}
    return {}


async def send_text(channel, conversation, text: str, *, human_agent: bool = False) -> str:
    if not text or (channel.provider == "instagram" and len(text.encode("utf-8")) > 1000) or (
        channel.provider == "messenger" and len(text) > 2000
    ):
        raise HTTPException(400, "The message exceeds this channel's text limit")
    account_id, need_tag = _delivery(channel, human=human_agent)
    thread_id = await _resolve_thread(channel, conversation)
    data = await provider.send_message(account_id, thread_id, message=text, **_tag_kwargs(human_agent, need_tag))
    return data["messageId"]


async def send_media(channel, conversation, kind: str, url: str, *, human_agent: bool = False) -> str:
    kind = "file" if kind == "document" else kind
    parsed = urlsplit(url)
    if kind not in {"image", "audio", "video", "file"} or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(400, "Use a supported attachment with a public HTTPS download URL")
    account_id, need_tag = _delivery(channel, human=human_agent)
    thread_id = await _resolve_thread(channel, conversation)
    data = await provider.send_message(
        account_id, thread_id, attachment_url=url, attachment_type=kind, **_tag_kwargs(human_agent, need_tag))
    return data["messageId"]


async def mark_read(channel, conversation) -> None:
    account_id, _ = _delivery(channel, human=True)
    try:
        await provider.mark_read(account_id, _thread(conversation))
    except HTTPException:
        pass


async def send_reaction(channel, conversation, platform_message_id: str, emoji: str) -> None:
    account_id, _ = _delivery(channel, human=True)
    thread_id = _thread(conversation)
    if emoji:
        await provider.send_reaction(account_id, thread_id, platform_message_id, emoji)
    else:
        await provider.remove_reaction(account_id, thread_id, platform_message_id)
