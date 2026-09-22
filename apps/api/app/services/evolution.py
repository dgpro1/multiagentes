"""Evolution API v2 driver for WhatsApp QR lines.

Implements the same behaviours the local Go bridge offered — QR pairing,
connection state, text/media/voice delivery, read receipts and reactions —
against a self-hosted Evolution API (https://docs.evolutionfoundation.com.br).
Every endpoint used here is taken from the official documentation and source
(see apps/api/tests/test_whatsapp_evolution.py for the exercised paths).
"""

import base64
import logging
import re
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import WhatsAppChannel, now_utc
from ..security import encrypt_secret
from .whatsapp_format import markdown_to_whatsapp

logger = logging.getLogger(__name__)

# The user-facing states the WhatsAppChannel.status column understands.
_STATUS_MAP = {"open": "connected", "connecting": "connecting", "close": "reconnecting"}


def configured() -> bool:
    settings = get_settings()
    return bool(settings.evolution_api_url.strip() and settings.evolution_api_key.strip())


def enabled() -> bool:
    """Whether the QR driver in charge is Evolution (forced or auto-selected)."""
    driver = get_settings().whatsapp_qr_driver.strip().lower()
    if driver == "evolution":
        return True
    if driver == "bridge":
        return False
    return configured()


def instance_name(channel: WhatsAppChannel) -> str:
    """One Evolution instance per line; deterministic, so restarts reuse it."""
    return f"openlivery-{channel.id}"


def webhook_url() -> str:
    settings = get_settings()
    base = settings.evolution_webhook_url.strip() or settings.backend_url.rstrip("/")
    return f"{base.rstrip('/')}/api/public/whatsapp/evolution/webhook"


def _headers() -> dict[str, str]:
    return {"apikey": get_settings().evolution_api_key.strip()}


async def request(method: str, path: str, *, json: dict | None = None, timeout: float = 30) -> Any:
    """Call the Evolution API, mapping failures like ``bridge_command`` does."""
    settings = get_settings()
    base = settings.evolution_api_url.strip().rstrip("/")
    if not base:
        raise HTTPException(status_code=409, detail="The Evolution API is not configured")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, f"{base}{path}", headers=_headers(), json=json)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="The Evolution API service is not available") from exc
    if response.status_code in (200, 201, 202):
        try:
            return response.json()
        except ValueError:
            return {}
    try:
        detail = response.json().get("error", {}).get("message")
    except ValueError:
        detail = None
    raise HTTPException(status_code=502, detail=detail or "Evolution API could not complete the operation")


def _state_of(instance: dict) -> str | None:
    status = instance.get("connectionStatus", instance.get("state"))
    if isinstance(status, dict):
        status = status.get("state")
    return status if isinstance(status, str) else None


async def fetch_instance(channel: WhatsAppChannel) -> dict | None:
    """The Evolution instance record for this line, or None when absent."""
    try:
        result = await request("GET", f"/instance/fetchInstances?instanceName={instance_name(channel)}")
    except HTTPException as exc:
        if exc.status_code == 502:  # 404 from Evolution: not created yet
            return None
        raise
    if isinstance(result, list):
        return result[0] if result else None
    if isinstance(result, dict) and result:
        return result
    return None


async def _create_instance(channel: WhatsAppChannel) -> dict:
    settings = get_settings()
    payload = {
        "instanceName": instance_name(channel),
        "qrcode": True,
        "integration": "WHATSAPP-BAILEYS",
        "webhook": {
            "enabled": True,
            "url": webhook_url(),
            "byEvents": False,
            "base64": True,
            "headers": {"Authorization": f"Bearer {settings.evolution_webhook_secret.strip()}"},
        },
    }
    return await request("POST", "/instance/create", json=payload)


async def ensure_instance(channel: WhatsAppChannel) -> dict:
    """The instance record, creating it (with the event webhook and this
    line's settings) when missing."""
    existing = await fetch_instance(channel)
    if existing:
        return existing
    created = await _create_instance(channel)
    await apply_settings(channel)
    return created


def _store_qr(channel: WhatsAppChannel, qr: str | None) -> None:
    if not qr:
        return
    if not qr.startswith("data:image"):
        qr = f"data:image/png;base64,{qr}"
    channel.encrypted_qr = encrypt_secret(qr)
    channel.status = "qr"


async def connect(channel: WhatsAppChannel) -> None:
    """Create the instance if needed and request a QR for pairing."""
    channel.status = "connecting"
    channel.last_error = None
    await ensure_instance(channel)
    try:
        result = await request("GET", f"/instance/connect/{instance_name(channel)}", timeout=60)
    except HTTPException as exc:
        channel.status = "error"
        channel.last_error = str(exc.detail)
        raise
    qr = (result.get("qrcode") or {}).get("base64") if isinstance(result, dict) else None
    _store_qr(channel, qr)
    channel.updated_at = now_utc()


async def disconnect(channel: WhatsAppChannel) -> None:
    """Log the phone out but keep the instance (a new QR can be requested)."""
    try:
        await request("DELETE", f"/instance/logout/{instance_name(channel)}")
    except HTTPException as exc:
        if exc.status_code != 502:  # logout of a never-connected instance 404s
            raise
    channel.encrypted_auth_state = None
    channel.encrypted_qr = None
    channel.phone_number = None
    channel.display_name = None
    channel.status = "disconnected"
    channel.is_enabled = False
    channel.updated_at = now_utc()


async def delete_instance(channel: WhatsAppChannel) -> None:
    """Remove the Evolution instance entirely. Best-effort: the local row is
    going away regardless, and a missing instance is already the goal."""
    try:
        await request("DELETE", f"/instance/delete/{instance_name(channel)}")
    except HTTPException as exc:
        if exc.status_code != 502:  # 404: nothing left to delete
            raise
        logger.info("Evolution instance for line %s was already gone", channel.id)


async def connection_state(channel: WhatsAppChannel) -> str:
    instance = await ensure_instance(channel)
    state = _state_of(instance) if instance else None
    if not state:
        try:
            result = await request("GET", f"/instance/connectionState/{instance_name(channel)}")
            state = (result.get("instance") or {}).get("state")
        except HTTPException:
            state = None
    return _STATUS_MAP.get(state or "", "connecting")


async def refresh_instance_state(channel: WhatsAppChannel) -> None:
    """Pull owner identity (number, display name) from the instance record."""
    instance = await fetch_instance(channel)
    if not instance:
        return
    owner = str(instance.get("ownerJid") or instance.get("number") or "")
    if owner:
        channel.phone_number = re.sub(r"\D", "", owner.split("@")[0])[:32]
    name = instance.get("profileName") or instance.get("name")
    if name:
        channel.display_name = str(name)[:120]
    channel.updated_at = now_utc()


def _find_media(payload: dict) -> tuple[str | None, str, str]:
    """Locate ``(kind, base64, mime)`` inside a MESSAGES_UPSERT message."""
    nodes = payload.get("message") or {}
    kinds = {
        "imageMessage": "image",
        "audioMessage": "audio",
        "videoMessage": "video",
        "documentMessage": "document",
        "stickerMessage": "image",
    }
    for node, kind in kinds.items():
        media = nodes.get(node)
        if not isinstance(media, dict):
            continue
        if node == "audioMessage" and media.get("ptt"):
            kind = "audio"
        encoded = media.get("base64")
        if not encoded:
            continue
        if encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[-1]
        return kind, encoded, str(media.get("mimetype") or "")
    return None, "", ""


async def send_text(channel: WhatsAppChannel, chat_jid: str, text: str, *, quoted_external_id: str | None = None) -> str | None:
    payload: dict = {
        "number": chat_jid,
        "text": markdown_to_whatsapp(text),
        "linkPreview": False,
    }
    if quoted_external_id:
        payload["quoted"] = {
            "key": {"remoteJid": chat_jid, "fromMe": False, "id": quoted_external_id},
            "message": {"conversation": ""},
        }
    try:
        result = await request("POST", f"/message/sendText/{instance_name(channel)}", json=payload)
    except HTTPException as exc:
        if quoted_external_id and exc.status_code == 502:
            # The quoted message may no longer be quotable; deliver plain text.
            payload.pop("quoted")
            result = await request("POST", f"/message/sendText/{instance_name(channel)}", json=payload)
        else:
            raise
    return ((result.get("key") or {}).get("id")) if isinstance(result, dict) else None


async def send_media(
    channel: WhatsAppChannel,
    chat_jid: str,
    *,
    kind: str,
    data: bytes,
    mime: str,
    filename: str | None = None,
    caption: str = "",
) -> str | None:
    """Send image/video/document/audio. Voice notes (ogg/opus) use the ``ptv``
    media type so WhatsApp renders them as playable voice messages."""
    is_voice = kind == "audio" and mime == "audio/ogg"
    payload = {
        "number": chat_jid,
        "mediatype": "ptv" if is_voice else kind,
        "mimetype": mime,
        "media": f"data:{mime};base64,{base64.b64encode(data).decode()}",
        "caption": markdown_to_whatsapp(caption),
    }
    if filename:
        payload["fileName"] = filename
    result = await request("POST", f"/message/sendMedia/{instance_name(channel)}", json=payload, timeout=90)
    return ((result.get("key") or {}).get("id")) if isinstance(result, dict) else None


async def mark_read(channel: WhatsAppChannel, chat_jid: str, message_ids: list[str], *, typing: bool) -> None:
    """Blue-tick the visitor messages; optionally hint the typing indicator."""
    if message_ids:
        await request(
            "POST",
            f"/chat/markMessageAsRead/{instance_name(channel)}",
            json={
                "readMessages": [
                    {"remoteJid": chat_jid, "id": item, "fromMe": False} for item in message_ids
                ]
            },
        )
    if typing:
        try:
            await request(
                "POST",
                f"/chat/sendPresence/{instance_name(channel)}",
                json={"number": chat_jid, "presence": "composing", "delay": 5000},
            )
        except HTTPException:
            pass  # presence is cosmetic


async def send_reaction(channel: WhatsAppChannel, chat_jid: str, external_message_id: str, emoji: str, *, target_from_me: bool = False) -> None:
    await request(
        "POST",
        f"/message/sendReaction/{instance_name(channel)}",
        json={
            "key": {"remoteJid": chat_jid, "fromMe": target_from_me, "id": external_message_id},
            "reaction": emoji,
        },
    )


def settings_payload(channel: WhatsAppChannel) -> dict:
    """The instance settings mirroring the line's toggles. Calls are always
    refused at the phone level; ``calls_enabled`` only controls whether the
    caller gets the line's explanation message. Groups are ignored entirely
    unless the line attends them."""
    message = (channel.calls_message or "").strip()
    return {
        "rejectCall": not channel.calls_enabled,
        "msgCall": message,
        "groupsIgnore": not channel.groups_enabled,
        "alwaysOnline": False,
        "readMessages": False,
        "readStatus": False,
        "syncFullHistory": False,
    }


async def apply_settings(channel: WhatsAppChannel) -> None:
    """Push the line's toggles to the instance. Best-effort: an offline
    Evolution must not break configure/connect flows."""
    try:
        await request("POST", f"/settings/set/{instance_name(channel)}", json=settings_payload(channel))
    except HTTPException:
        logger.info("Evolution settings could not be applied for line %s", channel.id)


async def send_location(
    channel: WhatsAppChannel,
    chat_jid: str,
    *,
    latitude: float,
    longitude: float,
    name: str = "",
    address: str = "",
) -> str | None:
    payload: dict = {
        "number": chat_jid,
        "latitude": latitude,
        "longitude": longitude,
    }
    if name.strip():
        payload["name"] = name.strip()
    if address.strip():
        payload["address"] = address.strip()
    result = await request("POST", f"/message/sendLocation/{instance_name(channel)}", json=payload)
    return ((result.get("key") or {}).get("id")) if isinstance(result, dict) else None


async def owner_jid(channel: WhatsAppChannel) -> str | None:
    """The phone's own JID, used to detect replies to the bot inside groups."""
    instance = await fetch_instance(channel)
    if not instance:
        return None
    owner = str(instance.get("ownerJid") or "")
    return owner or None


async def group_info(channel: WhatsAppChannel, group_jid: str) -> dict:
    try:
        return await request(
            "GET",
            f"/group/findGroupInfos/{instance_name(channel)}?groupJid={group_jid}",
            timeout=15,
        )
    except HTTPException:
        return {}  # a vanished or foreign group: the chat id stays the title


async def set_webhook(channel: WhatsAppChannel) -> None:
    """Re-assert the per-instance webhook (used after restores)."""
    settings = get_settings()
    await request(
        "POST",
        f"/webhook/set/{instance_name(channel)}",
        json={
            "webhook": {
                "enabled": True,
                "url": webhook_url(),
                "byEvents": False,
                "base64": True,
                "events": ["QRCODE_UPDATED", "CONNECTION_UPDATE", "MESSAGES_UPSERT", "MESSAGES_UPDATE"],
                "headers": {"Authorization": f"Bearer {settings.evolution_webhook_secret.strip()}"},
            }
        },
    )


async def restore_channel(channel: WhatsAppChannel) -> None:
    """Reconnect a previously paired line after an API restart."""
    state = await connection_state(channel)
    channel.status = state
    if state != "connected":
        channel.status = "connecting"
        try:
            await connect(channel)
            await set_webhook(channel)
        except HTTPException:
            pass  # the line will show its state; the operator can reconnect
    await apply_settings(channel)
    channel.updated_at = now_utc()
