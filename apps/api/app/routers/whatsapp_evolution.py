"""Public webhook for the Evolution API WhatsApp QR driver.

Evolution POSTs every subscribed event to one endpoint with the shared
secret in the Authorization header, so the payload is parsed only after the
constant-time token check passes. Verified bodies are acknowledged with 200
even on internal failures: Evolution retries 5xx deliveries for ~30 minutes,
and a payload that failed once fails on every retry.
"""

import asyncio
import base64
import binascii
import hmac
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..config import get_settings
from ..database import get_db
from ..models import Agent, Conversation, Message, WhatsAppChannel, now_utc
from ..schemas import WhatsAppOutgoing
from ..security import encrypt_secret
from ..services import evolution as evolution_driver
from ..services.phone_handover import record_outgoing
from ..services.whatsapp import send_channel_message
from ..services.whatsapp_inbound import InboundMessage, process_inbound, send_reply_attachments

public_router = APIRouter(prefix="/public/whatsapp/evolution", tags=["WhatsApp callbacks"])

logger = logging.getLogger("openlivery.evolution")
MAX_WEBHOOK_BYTES = 8 * 1024 * 1024  # media arrives inline as base64


def _authorized(request: Request) -> bool:
    secret = get_settings().evolution_webhook_secret.strip()
    if not secret:
        return False
    header = request.headers.get("authorization", "")
    return hmac.compare_digest(header, f"Bearer {secret}")


@public_router.post("/webhook")
async def receive_webhook(request: Request, db: Session = Depends(get_db)):
    if not _authorized(request):
        raise HTTPException(status_code=401, detail="Invalid webhook token")
    raw = await request.body()
    if len(raw) > MAX_WEBHOOK_BYTES:
        raise HTTPException(413, "The webhook payload is too large")
    try:
        event = json.loads(raw)
    except ValueError:
        return {"status": "ok"}
    if not isinstance(event, dict):
        return {"status": "ok"}
    # From here on always acknowledge with 200.
    try:
        await _dispatch(db, event)
    except Exception:  # noqa: BLE001 - never make Evolution retry
        logger.exception("Evolution event processing failed")
    return {"status": "ok"}


def _channel(db: Session, instance: str) -> WhatsAppChannel | None:
    if not instance.startswith("openlivery-"):
        return None
    try:
        channel_id = instance.removeprefix("openlivery-")
    except AttributeError:  # pragma: no cover - Python < 3.9 guard
        return None
    return db.scalar(
        select(WhatsAppChannel)
        .options(joinedload(WhatsAppChannel.agent).joinedload(Agent.client))
        .where(WhatsAppChannel.id == channel_id)
        .execution_options(populate_existing=True)
    )


async def _handle_qr(db: Session, channel: WhatsAppChannel, data: dict) -> None:
    qr = (data.get("qrcode") or {}).get("base64")
    if not qr:
        return
    qr = str(qr)
    if not qr.startswith("data:image"):
        qr = f"data:image/png;base64,{qr}"
    channel.encrypted_qr = encrypt_secret(qr)
    channel.status = "qr"
    channel.updated_at = now_utc()
    db.commit()


def _apply_state(channel: WhatsAppChannel, state: str) -> None:
    if state == "open":
        channel.status = "connected"
        channel.encrypted_qr = None
    elif state == "connecting":
        channel.status = "connecting"
    elif state == "close":
        channel.status = "reconnecting"


async def _handle_connection(db: Session, channel: WhatsAppChannel, data: dict) -> None:
    state = str(data.get("state") or "")
    _apply_state(channel, state)
    if channel.status == "connected":
        from ..routers.whatsapp import apply_connected

        apply_connected(db, channel)
        asyncio.create_task(_refresh_identity(channel.id))
    channel.updated_at = now_utc()
    db.commit()


async def _refresh_identity(channel_id) -> None:
    """Pull number/display name from the instance record once connected."""
    from ..database import new_session

    try:
        with new_session() as db:
            channel = db.get(WhatsAppChannel, channel_id)
            if channel:
                await evolution_driver.refresh_instance_state(channel)
                db.commit()
    except Exception:  # noqa: BLE001 - identity is decorative
        logger.exception("Evolution identity refresh failed")


def _media(payload: dict) -> tuple[str | None, bytes | None, str]:
    kind, encoded, mime = evolution_driver._find_media(payload)
    if not kind or not encoded:
        return None, None, ""
    try:
        return kind, base64.b64decode(encoded), mime
    except (binascii.Error, ValueError):
        return None, None, ""


MENTION_TOKENS = ("@hunterai", "@openlivery")


def _is_group(chat_jid: str) -> bool:
    return chat_jid.endswith("@g.us")


async def _group_title(channel: WhatsAppChannel, group_jid: str) -> str | None:
    info = await evolution_driver.group_info(channel, group_jid)
    subject = str(info.get("subject") or "") if isinstance(info, dict) else ""
    return subject or None


def _mentioned_bot(payload: dict) -> bool:
    """A group message triggers the agent when it names the bot or quotes one
    of its messages; everything else stays human-only."""
    nodes = payload.get("message") or {}
    if not isinstance(nodes, dict):
        return False
    extended = nodes.get("extendedTextMessage")
    if isinstance(extended, dict) and any(tok in str(extended.get("text") or "").lower() for tok in MENTION_TOKENS):
        return True
    for node in nodes.values():
        if isinstance(node, dict):
            context = node.get("contextInfo")
            if isinstance(context, dict) and context.get("participant"):
                return True
    return False


def _location(payload: dict) -> dict | None:
    node = (payload.get("message") or {}).get("locationMessage") if isinstance(payload.get("message"), dict) else None
    if not isinstance(node, dict):
        return None
    try:
        latitude = float(node.get("degreesLatitude"))
        longitude = float(node.get("degreesLongitude"))
    except (TypeError, ValueError):
        return None
    return {
        "latitude": latitude,
        "longitude": longitude,
        "name": str(node.get("name") or ""),
        "address": str(node.get("address") or ""),
    }


def _text(payload: dict) -> str:
    nodes = payload.get("message") or {}
    if not isinstance(nodes, dict):
        return ""
    for key in ("conversation", "extendedTextMessage"):
        node = nodes.get(key)
        if isinstance(node, dict):
            return str(node.get("text") or "")
        if isinstance(node, str):
            return node
    for key in ("imageMessage", "videoMessage", "documentMessage", "audioMessage", "stickerMessage"):
        node = nodes.get(key)
        if isinstance(node, dict) and node.get("caption"):
            return str(node.get("caption"))
    return ""


def _quoted(payload: dict) -> str | None:
    nodes = payload.get("message") or {}
    if not isinstance(nodes, dict):
        return None
    context = None
    extended = nodes.get("extendedTextMessage")
    if isinstance(extended, dict) and isinstance(extended.get("contextInfo"), dict):
        context = extended["contextInfo"]
    else:
        for node in nodes.values():
            if isinstance(node, dict) and isinstance(node.get("contextInfo"), dict):
                context = node["contextInfo"]
                break
    if isinstance(context, dict):
        return str(context.get("stanzaId") or "") or None
    return None


async def _handle_message(db: Session, channel: WhatsAppChannel, payload: dict) -> None:
    key = payload.get("key") or {}
    external_id = str(key.get("id") or "")
    chat_jid = str(key.get("remoteJid") or "")
    if not external_id or not chat_jid:
        return
    if key.get("fromMe"):
        # Mirror a message sent from the phone so the portal keeps the thread.
        record_outgoing(
            db,
            channel,
            WhatsAppOutgoing(
                external_message_id=external_id,
                remote_jid=chat_jid,
                text=_text(payload),
                media_kind=None,
                occurred_at=None,
            ),
        )
        db.commit()
        return
    if _is_group(chat_jid):
        if not channel.groups_enabled:
            return  # groups are off for this line
        if not _mentioned_bot(payload):
            return  # only mentions and replies to the bot trigger the agent
    place = _location(payload)
    kind, media_bytes, mime = _media(payload)
    media_kind = kind if kind in ("image", "audio", "video", "document") else None
    document_node = (payload.get("message") or {}).get("documentMessage") if isinstance(payload.get("message"), dict) else None
    result = await process_inbound(
        db,
        channel,
        InboundMessage(
            external_message_id=external_id,
            external_chat_id=chat_jid,
            sender_name=(str(payload.get("pushName") or "") or None),
            text=_text(payload),
            media_kind=media_kind,
            media_bytes=media_bytes,
            media_mime=mime or None,
            media_filename=str(document_node.get("fileName") or "") or None if isinstance(document_node, dict) else None,
            quoted_external_id=_quoted(payload),
            location=place,
            group_title=(await _group_title(channel, chat_jid)) if _is_group(chat_jid) else None,
        ),
        conversation_channel="whatsapp",
        channel_fk_field="whatsapp_channel_id",
    )
    # Unlike the internal /inbound endpoint (whose caller delivers result.reply
    # itself), the webhook has nobody to hand the reply to: send it through the channel.
    conversation = db.get(Conversation, result.conversation_id) if result.conversation_id else None
    if result.reply and conversation:
        try:
            external_id = await send_channel_message(
                db, conversation, result.reply, quoted_external_id=result.quote_external_id
            )
        except HTTPException as exc:
            channel.last_error = f"The reply could not be sent: {exc.detail}"
            channel.updated_at = now_utc()
            db.commit()
        else:
            if external_id and result.outbound_message_id:
                outbound = db.get(Message, result.outbound_message_id)
                if outbound:
                    outbound.external_message_id = external_id
                    db.commit()
    if result.attachment_message_ids and result.conversation_id:
        conversation = db.get(Conversation, result.conversation_id)
        if conversation:
            await send_reply_attachments(db, conversation, result.attachment_message_ids)


async def _dispatch(db: Session, event: dict) -> None:
    name = event.get("event")
    channel = _channel(db, str(event.get("instance") or ""))
    if not channel:
        return
    data = event.get("data") or {}
    if name == "QRCODE_UPDATED":
        await _handle_qr(db, channel, data)
    elif name == "CONNECTION_UPDATE":
        await _handle_connection(db, channel, data)
    elif name == "MESSAGES_UPSERT":
        if isinstance(data, dict) and data.get("key"):
            await _handle_message(db, channel, data)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("key"):
                    await _handle_message(db, channel, item)
