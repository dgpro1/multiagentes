"""Single public callback for the unified messaging provider.

The provider signs every delivery with HMAC-SHA256 over the raw bytes
using the server webhook secret, so the payload is parsed only after the
signature check passes. Bodies are acknowledged with 200 once verified:
the provider retries anything else, and a payload that fails once fails
on every retry.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user
from ..models import Client, Conversation, Message, SocialChannel, SocialOutbox, User, WhatsAppCloudChannel, now_utc
from ..ratelimit import whatsapp_cloud_webhook_rate_limit
from ..services import messaging_provider as provider
from ..services import portal_return
from ..services.social_inbound import enqueue_provider_event, event_time
from ..services.whatsapp_format import markdown_to_whatsapp
from ..services.whatsapp_inbound import InboundMessage, process_inbound, send_reply_attachments

public_router = APIRouter(prefix="/public/messaging", tags=["Messaging callbacks"])
router = APIRouter(prefix="/messaging", tags=["Messaging provider"])

logger = logging.getLogger("openlivery.messaging")
MAX_WEBHOOK_BYTES = 2 * 1024 * 1024


def _digits(value: str | None) -> str:
    return "".join(char for char in (value or "") if char.isdigit())


@public_router.post("/webhook", dependencies=[Depends(whatsapp_cloud_webhook_rate_limit)])
async def receive_webhook(request: Request, db: Session = Depends(get_db)):
    secret = get_settings().messaging_provider_webhook_secret.strip()
    if not secret:
        raise HTTPException(status_code=403, detail="Channel is not configured")
    raw = await request.body()
    if len(raw) > MAX_WEBHOOK_BYTES:
        raise HTTPException(413, "The webhook payload is too large")
    signature = provider.signature_from_headers(request.headers)
    if not provider.verify_signature(raw, signature, secret):
        raise HTTPException(status_code=403, detail="Invalid signature")

    # From here on always acknowledge with 200.
    try:
        event = json.loads(raw)
    except ValueError:
        return {"status": "ok"}
    if not isinstance(event, dict):
        return {"status": "ok"}
    try:
        await _dispatch(db, event)
    except Exception as exc:
        logger.error("Provider event processing failed (%s)", type(exc).__name__)
    return {"status": "ok"}


async def _dispatch(db: Session, event: dict) -> None:
    name = event.get("event")
    if name == "webhook.test":
        return
    if name in ("message.received", "message.sent", "message.delivered", "message.read",
                "message.failed", "message.edited", "message.deleted", "reaction.received",
                "conversation.started", "conversation.control_changed"):
        message = event.get("message") or {}
        platform = str(message.get("platform") or "")
        account = event.get("account") or {}
        account_id = str(account.get("accountId") or event.get("accountId") or "")
        if not platform or not account_id:
            return
        if platform == "whatsapp":
            await _dispatch_whatsapp(db, name, event, account_id)
        elif platform in ("instagram", "facebook"):
            if name in ("message.received", "message.sent", "message.delivered",
                        "message.read", "reaction.received", "message.edited"):
                enqueue_provider_event(db, event, commit=True)
            elif name == "message.failed":
                _fail_social_outbox(db, event, account_id)
        return
    if name in ("account.connected", "account.disconnected"):
        _account_status(db, event, connected=name == "account.connected")
        return
    # conversation.started needs nothing (cases open on the first message);
    # template verdicts are read live; anything else is ignored.


# WhatsApp numbers.

def _sender_chat(message: dict) -> tuple[str, str | None]:
    sender = message.get("sender") or {}
    phone = _digits(sender.get("phoneNumber"))
    if phone:
        return phone, sender.get("name")
    return str(sender.get("id") or ""), sender.get("name")


def parse_inbound(message: dict) -> InboundMessage | None:
    text = message.get("text") or ""
    metadata = message.get("metadata") or {}
    if not text and metadata.get("interactiveId"):
        text = str(metadata.get("interactiveId"))
    sender_id, sender_name = _sender_chat(message)
    base = {
        "external_message_id": str(message.get("platformMessageId") or ""),
        "external_chat_id": sender_id,
        "sender_name": sender_name,
        "sender_user_id": sender_id,
        "occurred_at": event_time((message.get("timestamp") or "")),
        "provider_conversation_id": str(message.get("conversationId") or "") or None,
    }
    if not base["external_message_id"] or not sender_id:
        return None
    attachments = message.get("attachments") or []
    media = attachments[0] if attachments else {}
    kind = str(media.get("type") or "")
    quoted = metadata.get("quotedMessageId")
    if kind in ("image", "audio", "video", "file"):
        return InboundMessage(
            **base, text=text,
            media_kind=kind if kind in ("image", "audio", "video") else "file",
            media_url=str(media.get("url") or ""),
            quoted_external_id=str(quoted) if quoted else None,
        )
    if text:
        return InboundMessage(**base, text=text, quoted_external_id=str(quoted) if quoted else None)
    return None


async def _dispatch_whatsapp(db: Session, name: str, event: dict, account_id: str) -> None:
    channel = db.scalar(select(WhatsAppCloudChannel).where(
        WhatsAppCloudChannel.external_account_id == account_id))
    if not channel or not channel.is_enabled:
        return
    message = event.get("message") or {}
    platform_id = str(message.get("platformMessageId") or "")
    if name == "message.received":
        if str(message.get("direction") or "") == "outgoing":
            return
        await handle_whatsapp_message(db, channel, event, message)
    elif name in ("message.delivered", "message.read", "message.sent"):
        if platform_id:
            state = {"message.sent": "sent", "message.delivered": "delivered", "message.read": "read"}[name]
            stamp_receipt(db, channel, platform_id, state, None)
    elif name == "message.failed":
        if platform_id:
            detail = message.get("error") or (message.get("deliveryError") or {}).get("message") or "no error detail provided"
            stamp_receipt(db, channel, platform_id, "failed", str(detail)[:400])
    elif name == "reaction.received":
        apply_whatsapp_reaction(db, channel, event, message)
    elif name == "message.edited":
        _apply_whatsapp_edit(db, channel, message)
    elif name == "message.deleted":
        _apply_whatsapp_delete(db, channel, message)


_DELIVERY_ORDER = {"sent": 1, "delivered": 2, "read": 3, "failed": 4}


def stamp_receipt(db: Session, channel: WhatsAppCloudChannel, platform_id: str, state: str, error: str | None) -> None:
    """Stamp the receipt on the message it concerns. Receipts can arrive out
    of order, so a later stage is never downgraded by an earlier one."""
    row = db.scalar(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.external_message_id == platform_id, Conversation.whatsapp_cloud_channel_id == channel.id)
    )
    if not row:
        return
    if _DELIVERY_ORDER[state] < _DELIVERY_ORDER.get(row.delivery_status or "", 0):
        return
    row.delivery_status = state
    row.delivery_error = error
    db.commit()


def _find_whatsapp_message(db: Session, channel: WhatsAppCloudChannel, event: dict, message: dict):
    platform_id = str(message.get("platformMessageId") or "")
    if not platform_id:
        return None, None
    conversation_id = str(message.get("conversationId") or "")
    query = select(Message).join(Conversation, Conversation.id == Message.conversation_id).where(
        Message.external_message_id == platform_id,
        Conversation.whatsapp_cloud_channel_id == channel.id,
    )
    if conversation_id:
        query = query.where(Conversation.provider_conversation_id == conversation_id)
    return db.scalar(query), platform_id


def apply_whatsapp_reaction(db: Session, channel: WhatsAppCloudChannel, event: dict, message: dict) -> None:
    reaction = event.get("reaction") or {}
    platform_id = str(reaction.get("platformMessageId") or message.get("platformMessageId") or "")
    if not platform_id:
        return
    target, _ = _find_whatsapp_message(db, channel, event, {"platformMessageId": platform_id})
    if not target:
        return
    removed = reaction.get("action") == "removed" or not reaction.get("emoji")
    target.incoming_reaction = None if removed else str(reaction.get("emoji") or "")[:16] or None
    db.commit()


def _apply_whatsapp_edit(db: Session, channel: WhatsAppCloudChannel, message: dict) -> None:
    platform_id = str(message.get("platformMessageId") or "")
    if not platform_id:
        return
    row = db.scalar(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.external_message_id == platform_id,
               Conversation.whatsapp_cloud_channel_id == channel.id, Message.role == "user")
    )
    if row and message.get("text"):
        row.content = str(message["text"])
        db.commit()


def _apply_whatsapp_delete(db: Session, channel: WhatsAppCloudChannel, message: dict) -> None:
    platform_id = str(message.get("platformMessageId") or "")
    if not platform_id:
        return
    row = db.scalar(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.external_message_id == platform_id,
               Conversation.whatsapp_cloud_channel_id == channel.id, Message.role == "user")
    )
    if row:
        db.delete(row)
        db.commit()


def _adopt_phone(db: Session, channel: WhatsAppCloudChannel, conversation: Conversation, inbound) -> None:
    """The same person can share their phone number on one message and hide
    it on the next. The provider thread stays the same, so fold the fresh
    peer profile into the case's contact instead of splitting the person in
    two. A peer with cases of its own only lends its number when nobody
    else holds it."""
    from sqlalchemy import update

    from ..models import ContactIdentity
    from ..services.contacts import find_contact, resolve_contact
    from ..services.whatsapp_identity import resolve_peer_contact

    peer = resolve_peer_contact(db, channel, inbound.external_chat_id,
                                name=inbound.sender_name, sender_user_id=inbound.sender_user_id)
    primary = conversation.contact
    if peer is None or primary is None or peer.id == primary.id:
        return
    others = db.scalars(select(Conversation.id).where(
        Conversation.contact_id == peer.id, Conversation.id != conversation.id).limit(1)).all()
    if others:
        # The peer has cases of its own: histories stay untouched rather
        # than moving a number between people on a guess.
        return
    phone = (peer.phone or "").strip()
    db.execute(update(ContactIdentity).where(ContactIdentity.contact_id == peer.id).values(contact_id=primary.id))
    if not primary.name.strip() and peer.name.strip():
        primary.name = peer.name.strip()[:180]
    if phone and not primary.phone:
        peer.phone = None
        db.flush()
        if not find_contact(db, channel.client_id, phone):
            primary.phone = phone
        else:
            peer.phone = phone
            db.flush()
    if peer.phone is None:
        db.flush()
        resolve_contact(db, channel.client_id, phone=primary.phone, name=primary.name)
        db.delete(peer)
    db.commit()


async def handle_whatsapp_message(db: Session, channel: WhatsAppCloudChannel, event: dict, message: dict) -> None:
    from ..services.whatsapp_cloud import fetch_media, send_text

    inbound = parse_inbound(message)
    if not inbound:
        return
    if getattr(inbound, "media_url", None) and inbound.media_kind:
        try:
            inbound.media_bytes, mime = await fetch_media(inbound.media_url)
            inbound.media_mime = inbound.media_mime or mime
        except HTTPException:
            inbound.media_bytes = None
    try:
        result = await process_inbound(
            db, channel, inbound,
            conversation_channel="whatsapp_cloud", channel_fk_field="whatsapp_cloud_channel_id",
        )
    except Exception as exc:
        channel.last_error = f"An inbound message could not be processed: {str(exc)[:400]}"
        channel.updated_at = now_utc()
        db.commit()
        return
    conversation = db.get(Conversation, result.conversation_id) if result.conversation_id else None
    thread_id = str(message.get("conversationId") or "")
    if conversation and thread_id and conversation.provider_conversation_id != thread_id:
        conversation.provider_conversation_id = thread_id
        db.commit()
    if conversation:
        _adopt_phone(db, channel, conversation, inbound)
    if not channel.external_account_id or not conversation or not thread_id:
        return
    if result.reply:
        try:
            external_id = await send_text(
                channel.external_account_id, thread_id,
                markdown_to_whatsapp(result.reply),
                context_message_id=result.quote_external_id,
            )
        except HTTPException as exc:
            channel.last_error = f"The reply could not be sent: {exc.detail}"
            channel.updated_at = now_utc()
            db.commit()
            return
        if external_id and result.outbound_message_id:
            stored = db.get(Message, result.outbound_message_id)
            if stored:
                stored.external_message_id = external_id
                db.commit()
    if result.attachment_message_ids:
        await send_reply_attachments(db, conversation, result.attachment_message_ids)


# Social accounts (durable worker pipeline).

def _fail_social_outbox(db: Session, event: dict, account_id: str) -> None:
    from ..services.social_delivery import _finish_message

    message = event.get("message") or {}
    platform_id = str(message.get("platformMessageId") or "")
    if not platform_id:
        return
    rows = db.scalars(select(SocialOutbox).where(SocialOutbox.external_message_id == platform_id)).all()
    for row in rows:
        row.status = "failed"
        row.last_error = str(message.get("error") or "The message was not delivered")[:400]
        row.locked_until = None
        db.flush()
        _finish_message(db, row, row.last_error)
    if rows:
        db.commit()


def _account_status(db: Session, event: dict, *, connected: bool) -> None:
    account = event.get("account") or {}
    account_id = str(account.get("accountId") or event.get("accountId") or "")
    if not account_id:
        return
    for model in (WhatsAppCloudChannel, SocialChannel):
        field = model.external_account_id
        rows = db.scalars(select(model).where(field == account_id)).all()
        for row in rows:
            if connected:
                if row.status != "connected":
                    row.status = "connected"
                    row.is_enabled = True
                    row.last_error = None
                    row.last_connected_at = row.last_connected_at or now_utc()
                    row.updated_at = now_utc()
            else:
                row.status = "disconnected"
                row.is_enabled = False
                row.last_error = "The messaging account was disconnected on the provider"
                row.updated_at = now_utc()
    db.commit()


# Provider OAuth landing.

def _landing(url: str, *, line: str | None, status: str) -> RedirectResponse:
    """Back to the product with the outcome attached, so the channel page
    can report it and clean the address."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    if line:
        query["line"] = line
    query["messaging_status"] = status
    return RedirectResponse(urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)),
                            status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def _client_screen(db: Session, client_id, frontend: str) -> str:
    """Where a failed connection with no known channel type lands: the portal's
    screen for a flow a portal admin started, the client's channels tab otherwise."""
    default = f"{frontend}/clients/{client_id}?tab=channels"
    for flow in ("instagram", "messenger"):
        target = portal_return.landing(db, client_id, flow, "")
        if target:
            return target
    return default


@public_router.get("/connect/callback")
async def connect_callback(
    request: Request, db: Session = Depends(get_db),
    connected: str | None = None, profileId: str | None = None,
    accountId: str | None = None, error: str | None = None,
):
    """Where the hosted authorization page lands. Binds the approved account
    to its channel and sends the operator back to the product."""
    _ = request
    frontend = get_settings().frontend_url.rstrip("/")
    channel = db.scalar(select(WhatsAppCloudChannel).where(
        WhatsAppCloudChannel.provider_profile_id == profileId).limit(1)) if profileId else None
    client = channel.client if channel else (
        db.scalar(select(Client).where(Client.provider_profile_id == profileId).limit(1)) if profileId else None)
    if channel is None and client is None:
        raise HTTPException(status_code=400, detail="This connection request is invalid or expired")
    if channel:
        cloud_screen = portal_return.landing(
            db, channel.client_id, portal_return.CLOUD_FLOW,
            f"{frontend}/clients/{channel.client_id}/channels/whatsapp-cloud", consume=True,
        )
    if error or connected == "error" or not accountId:
        target = cloud_screen if channel else _client_screen(db, client.id, frontend)
        return _landing(target, line=str(channel.id) if channel else None, status="error")
    if channel:
        from ..services.whatsapp_cloud import verify_account

        channel.external_account_id = accountId
        channel.coexistence = False
        try:
            profile = await verify_account(accountId, profileId)
        except HTTPException as exc:
            channel.status = "error"
            channel.last_error = str(exc.detail)[:400]
            channel.updated_at = now_utc()
            db.commit()
            return _landing(cloud_screen, line=str(channel.id), status="error")
        channel.status = "connected"
        channel.phone_number = profile.get("display_phone_number")
        channel.display_name = profile.get("verified_name")
        channel.quality_rating = profile.get("quality_rating")
        channel.messaging_limit = profile.get("messaging_limit")
        channel.last_error = None
        channel.is_enabled = True
        channel.last_connected_at = now_utc()
        channel.updated_at = now_utc()
        db.commit()
        return _landing(cloud_screen, line=str(channel.id), status="ready")
    client = db.scalar(select(Client).where(Client.provider_profile_id == profileId).limit(1))
    if not client:
        raise HTTPException(status_code=400, detail="This connection request is invalid or expired")
    platform = str(request.query_params.get("platform") or "")
    provider_name = {"whatsapp": None, "instagram": "instagram", "facebook": "messenger"}.get(platform)
    if provider_name is None and platform:
        raise HTTPException(status_code=400, detail="Unsupported messaging channel")
    if provider_name is None:
        # The hosted page reports the platform only on some channels; infer
        # it from the account the profile holds.
        try:
            remote = await provider.require_account(accountId, profileId)
        except HTTPException:
            return _landing(_client_screen(db, client.id, frontend), line=None, status="error")
        remote_platform = str(remote.get("platform") or "")
        provider_name = {"instagram": "instagram", "facebook": "messenger"}.get(remote_platform)
        if not provider_name:
            return _landing(_client_screen(db, client.id, frontend), line=None, status="error")
    from ..services import social_connections as connections

    try:
        bound, next_url = await connections.bind_callback_account(db, provider_name, profileId, accountId)
    except HTTPException:
        screen = portal_return.landing(db, client.id, provider_name, f"{frontend}/clients/{client.id}/channels/{provider_name}")
        return _landing(screen, line=None, status="error")
    return _landing(next_url, line=str(bound.id), status="ready")


@public_router.get("/samples/{handle}")
async def sample(handle: str, db: Session = Depends(get_db)):
    from ..services import messaging_media

    _ = db
    if not handle or len(handle) > 64:
        raise HTTPException(status_code=404, detail="Sample not found")
    data, mime, name = messaging_media.read_sample(handle)
    from ..services.attachments import content_disposition

    return Response(data, media_type=mime,
        headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff",
                 "Content-Security-Policy": "default-src 'none'; sandbox", "Referrer-Policy": "no-referrer",
                 "Content-Disposition": content_disposition(name)})


@router.post("/webhook/ensure")
async def ensure_webhook(user: User = Depends(get_current_user)):
    """Register (or repair) the shared event subscription on the provider."""
    _ = user
    provider.require_config()
    secret = get_settings().messaging_provider_webhook_secret.strip()
    if not secret:
        raise HTTPException(status_code=409, detail="Set MESSAGING_PROVIDER_WEBHOOK_SECRET first")
    webhook = await provider.ensure_webhook("OpenLivery inbox", provider.webhook_url(), secret, provider.INBOX_EVENTS)
    return {"url": webhook.get("url"), "events": webhook.get("events"), "active": webhook.get("isActive", True)}


# The names these had before they were public. Kept for one release.
_parse_inbound = parse_inbound
_stamp_receipt = stamp_receipt
_apply_whatsapp_reaction = apply_whatsapp_reaction
_handle_whatsapp_message = handle_whatsapp_message
