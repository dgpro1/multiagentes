"""Companion-app guards for WhatsApp API numbers.

Numbers are served through the unified messaging provider; the reply
window and the per-chat locks below are transport-independent and stay.
The retired direct-sync machinery is gone: numbers linked through the
provider always run Cloud-API-only, and stale sync rows drain as
processed so an upgrade never leaves work stuck.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select, text

from ..database import new_session
from ..models import WhatsAppCloudChannel, WhatsAppCoexistenceEvent, now_utc

logger = logging.getLogger(__name__)

# No webhook fields route here anymore; the unified provider webhook owns
# every event. Kept so old call sites keep importing.
FIELDS = frozenset()

_task = None
_scope_runner = None


def lock_chats(db, channel_id, peers):
    keys = sorted({int.from_bytes(hashlib.sha256(f"{channel_id}:{peer}".encode()).digest()[:8], "big", signed=True) for peer in peers})
    if keys:
        db.execute(text("SELECT pg_advisory_xact_lock(key) FROM unnest(CAST(:keys AS bigint[])) AS key ORDER BY key"), {"keys": keys})


def window_fields(conversation):
    channel = conversation.whatsapp_cloud_channel
    last = conversation.social_last_inbound_at
    until = last + timedelta(hours=24) if last else None
    reason = None
    if not channel or not channel.is_enabled or channel.status != "connected":
        reason = "channel_disconnected"
    elif not until or until <= now_utc():
        reason = "reply_window_closed"
    return {"reply_window_until": until, "reply_window_open": reason is None,
            "human_reply_window_until": until, "human_reply_window_open": reason is None, "reply_block_reason": reason}


def require_reply(conversation):
    channel = conversation.whatsapp_cloud_channel
    if channel and channel.coexistence and not window_fields(conversation)["reply_window_open"]:
        raise HTTPException(status_code=409, detail="Wait for a new customer message before replying from the Inbox, or reply from WhatsApp Business on your phone.")


def sync_state(db, channel, section: str, **values):
    # Another transaction may have received progress while a call ran.
    channel = db.scalar(select(WhatsAppCloudChannel).where(WhatsAppCloudChannel.id == channel.id)
                        .with_for_update().execution_options(populate_existing=True))
    state = dict(channel.coexistence_sync or {})
    state[section] = {**state.get(section, {}), **values}
    channel.coexistence_sync = state
    db.flush()
    return channel


def mark_disconnected(channel, *, offboarded: bool, message: str):
    channel.status = "disconnected"
    channel.is_enabled = False
    channel.last_error = message
    channel.updated_at = now_utc()
    key = "offboarded_at" if offboarded else "authorization_lost_at"
    channel.coexistence_sync = {**(channel.coexistence_sync or {}), key: now_utc().isoformat()}


async def refresh_connection(db, channel):
    """Reconcile without registering, disconnecting, or sending anything:
    the number's display name and quality as the provider has them now."""
    from .whatsapp_cloud import verify_account

    if not channel.external_account_id:
        if channel.is_enabled or channel.status == "connected":
            mark_disconnected(channel, offboarded=False, message="WhatsApp authorization is missing. Connect the account again.")
            db.commit()
        return
    channel_id = channel.id
    expected = (channel.external_account_id, channel.last_connected_at)
    try:
        profile = await verify_account(channel.external_account_id, channel.provider_profile_id)
    except HTTPException as exc:
        current = db.get(WhatsAppCloudChannel, channel_id)
        if current and (current.external_account_id, current.last_connected_at) == expected:
            mark_disconnected(current, offboarded=False, message=str(exc.detail)[:400])
            db.commit()
        return
    current = db.scalar(select(WhatsAppCloudChannel).where(WhatsAppCloudChannel.id == channel_id)
                        .with_for_update().execution_options(populate_existing=True))
    if not current or (current.external_account_id, current.last_connected_at) != expected:
        return
    current.display_name = profile.get("verified_name") or current.display_name or None
    current.phone_number = profile.get("display_phone_number") or current.phone_number or None
    rating = str(profile.get("quality_rating") or "").upper()
    if rating and rating != "NA":
        current.quality_rating = rating[:20]
    if profile.get("messaging_limit"):
        current.messaging_limit = str(profile["messaging_limit"]).upper()[:30]
    current.coexistence_sync = {**(current.coexistence_sync or {}), "status_checked_at": now_utc().isoformat()}
    db.commit()


def accept_name_update(db, channel, value: dict) -> bool:
    return False


def accept_quality_update(db, channel, value: dict) -> bool:
    return False


def accept_account_restriction(db, channel, value: dict, *, waba_id: str = "") -> bool:
    return False


def accept_change(db, channel, field: str, value: dict, *, waba_id: str = "") -> bool:
    """Retired direct-sync events are acknowledged and ignored."""
    return False


async def request_sync(db, channel):
    """Retired: numbers sync through the provider link itself."""
    return None


async def process_pending(db, *, limit=2, batch_size=100):
    """Drain retired sync rows so an upgrade never leaves work stuck."""
    rows = db.scalars(select(WhatsAppCoexistenceEvent).where(
        WhatsAppCoexistenceEvent.processed_at.is_(None)).limit(limit * batch_size)).all()
    for row in rows:
        row.processed_at = now_utc()
    if rows:
        db.commit()
    return len(rows)


async def run_scope(db):
    await process_pending(db)


def install_scope_runner(runner):
    global _scope_runner
    _scope_runner = runner


async def _loop():
    from ..config import get_settings

    while True:
        await asyncio.sleep(max(30.0, 30.0))
        try:
            if _scope_runner:
                await _scope_runner(run_scope)
            else:
                with new_session() as db:
                    await run_scope(db)
        except Exception as exc:
            logger.error("Companion sync iteration failed (%s)", type(exc).__name__)
            _ = get_settings()


def start_worker():
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


async def stop_worker():
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
