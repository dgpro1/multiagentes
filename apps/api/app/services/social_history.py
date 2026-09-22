"""Bounded, resumable imports of history still exposed by the provider."""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..models import SocialChannel, now_utc
from . import messaging_provider as provider
from .social_connections import owned_channel
from .social_inbound import enqueue_webhook, event_time

MAX_CONVERSATIONS = 20
MAX_MESSAGES = 20


def public_job(job):
    fields = ("id", "status", "conversations_count", "messages_count", "max_conversations", "last_error", "created_at", "updated_at")
    return {**{field: getattr(job, field) for field in fields}, "limited": True, "has_more": bool(job.cursor)}


def latest_job(db, channel):
    from ..models import SocialHistoryImport
    return db.scalar(select(SocialHistoryImport).where(SocialHistoryImport.channel_id == channel.id)
                     .order_by(SocialHistoryImport.created_at.desc()).limit(1))


def request_import(db, user, client_id, provider_name):
    from ..models import SocialHistoryImport
    channel = owned_channel(db, user, client_id, provider_name)
    if not channel.is_enabled or channel.status != "connected" or not channel.last_connected_at:
        raise HTTPException(409, "Connect this account before importing history")
    prior = latest_job(db, channel)
    if prior and prior.status in {"pending", "processing"}:
        return prior
    # A subsequent batch continues from the checkpoint of the prior batch.
    resume = bool(prior and prior.cursor)
    job = SocialHistoryImport(channel_id=channel.id, requested_by=user.id,
        cutoff_at=min(prior.cutoff_at, channel.last_connected_at) if resume else channel.last_connected_at,
        cursor=prior.cursor if resume else None, max_conversations=MAX_CONVERSATIONS)
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        active = latest_job(db, channel)
        if active and active.status in {"pending", "processing"}:
            return active
        raise HTTPException(409, "A history import is already being scheduled") from None
    db.refresh(job)
    return job


def _sender_of(item: dict) -> str:
    sender = item.get("sender") if isinstance(item.get("sender"), dict) else {}
    return str(item.get("senderId") or sender.get("id") or "")


def normalize_message(channel, thread: dict, item: dict, cutoff) -> dict | None:
    """Only import one-to-one messages addressing this receiving account."""
    if not isinstance(item, dict):
        return None
    occurred = event_time(item.get("createdAt"))
    mid = str(item.get("id") or "")
    if not occurred or occurred > cutoff or not mid or len(mid) > 1024:
        return None
    account_id = channel.external_account_id
    person = str(thread.get("participantId") or "")
    if not person or person == account_id:
        return None
    echo = item.get("direction") == "outgoing"
    text = str(item.get("message") or "")
    message: dict = {"mid": mid, "text": text or "[Historical attachment unavailable]"}
    if echo:
        message["is_echo"] = True
    sender = {"id": account_id} if echo else {
        "id": person, "name": thread.get("participantName"), "username": thread.get("participantUsername")}
    recipient = {"id": person} if echo else {"id": account_id}
    stamp = int(occurred.timestamp() * 1000)
    return {"sender": sender, "recipient": recipient, "timestamp": stamp,
            "provider_conversation_id": str(thread.get("id") or ""),
            "message": message, "_historical": True}


async def _read_page(channel, cursor: str | None, cutoff):
    threads, next_cursor = await provider.list_conversations(
        account_id=channel.external_account_id, limit=MAX_CONVERSATIONS, cursor=cursor)
    events: list[dict] = []
    for thread in threads:
        if not isinstance(thread, dict) or not thread.get("id"):
            continue
        items, _ = await provider.list_messages(
            channel.external_account_id, str(thread["id"]), limit=MAX_MESSAGES, sort_order="asc")
        for item in items[:MAX_MESSAGES]:
            event = normalize_message(channel, thread, item, cutoff)
            if event:
                events.append(event)
        if len(events) >= MAX_CONVERSATIONS * MAX_MESSAGES:
            break
    events.sort(key=lambda event: event["timestamp"])
    return events, next_cursor


async def process_history_jobs(db, *, limit: int = 1) -> int:
    """One bounded page per lease; restart resumes its committed cursor."""
    from ..models import SocialHistoryImport
    processed = 0
    for _ in range(limit):
        current = now_utc()
        job = db.scalar(select(SocialHistoryImport).where(
            SocialHistoryImport.status.in_(("pending", "processing")),
            (SocialHistoryImport.locked_until.is_(None)) | (SocialHistoryImport.locked_until <= current),
        ).order_by(SocialHistoryImport.created_at).with_for_update(skip_locked=True).limit(1))
        if not job:
            db.rollback()
            break
        job.status = "processing"
        job.locked_until = current + timedelta(minutes=5)
        job.updated_at = current
        db.commit()
        channel = db.get(SocialChannel, job.channel_id)
        try:
            if not channel or not channel.is_enabled or channel.status != "connected" or not channel.last_connected_at:
                raise HTTPException(409, "The messaging channel was disconnected. Reconnect it before importing history")
            events, cursor = await _read_page(channel, job.cursor, min(job.cutoff_at, channel.last_connected_at))
            count = enqueue_webhook(db, channel.provider, {"object": "instagram" if channel.provider == "instagram" else "page",
                "entry": [{"id": channel.external_account_id, "messaging": events}]}, channel, commit=False)
            job.cursor = cursor
            job.messages_count += count
            job.conversations_count += 1
            job.status = "completed" if not cursor or job.conversations_count >= job.max_conversations else "pending"
            job.last_error = None
        except HTTPException as exc:
            db.rollback()
            job.status = "failed"
            job.last_error = str(exc.detail)
            if channel and exc.status_code in {401, 403}:
                channel.status = "error"
                channel.last_error = str(exc.detail)
        except Exception:
            db.rollback()
            job.status = "failed"
            job.last_error = "The available history could not be imported. Retry from the last saved checkpoint"
        job.locked_until = None
        job.updated_at = now_utc()
        db.commit()
        processed += 1
    return processed
