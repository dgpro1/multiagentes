"""Outbound webhooks: fan event rows out to third-party URLs.

Emitting only writes rows; the worker sends them. Each delivery carries an
HMAC-SHA256 signature over its raw body in ``X-Signature`` so the receiver
can trust it came from here. A failed delivery climbs the 5/15/15/60-minute
ladder and then rests as ``failed`` in the log, where an operator replays
it by hand. Delivery is at-least-once and unordered by design.
"""

import hashlib
import hmac
import json
import logging
import secrets
from datetime import timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, aliased

from ..models import ApiIntegration, WebhookDelivery, WebhookSubscription, now_utc
from ..security import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

MESSAGE_RECEIVED = "message.received"
CONVERSATION_RESOLVED = "conversation.resolved"
DEAL_MOVED = "deal.moved"

EVENTS = (MESSAGE_RECEIVED, CONVERSATION_RESOLVED, DEAL_MOVED)

SIGNATURE_HEADER = "X-Signature"
SECRET_PREFIX = "whsec_"
SEND_TIMEOUT = 10
BATCH_LIMIT = 25
# Minutes to wait after each failed attempt before the next one.
RETRY_LADDER = (5, 15, 15, 60)


def new_secret() -> str:
    return f"{SECRET_PREFIX}{secrets.token_urlsafe(32)}"


def signature(secret: str, raw: bytes) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _payload(delivery: WebhookDelivery) -> dict:
    return {
        "id": str(delivery.id),
        "event": delivery.event,
        "occurred_at": delivery.created_at.isoformat() if delivery.created_at else None,
        "data": delivery.payload or {},
    }


def emit(db: Session, *, agency_id, client_id, event: str, data: dict) -> int:
    """Fan one event out to every matching subscription. Rides the caller's
    transaction (flush only): a later rollback takes the rows with it."""
    if event not in EVENTS:
        raise ValueError(f"Unknown webhook event: {event}")
    rows = db.scalars(
        select(WebhookSubscription)
        .join(ApiIntegration, ApiIntegration.id == WebhookSubscription.integration_id)
        .where(
            ApiIntegration.agency_id == agency_id,
            ApiIntegration.revoked_at.is_(None),
            WebhookSubscription.is_active.is_(True),
        )
    ).all()
    count = 0
    for row in rows:
        if event not in (row.events or []):
            continue
        if row.integration.client_id is not None and str(row.integration.client_id) != str(client_id):
            continue
        db.add(WebhookDelivery(
            subscription_id=row.id, event=event,
            payload={"agency_id": str(agency_id), "client_id": str(client_id), **data},
        ))
        count += 1
    if count:
        db.flush()
    return count


async def _post(url: str, secret: str, payload: dict) -> int:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    try:
        async with httpx.AsyncClient(timeout=SEND_TIMEOUT) as client:
            response = await client.post(
                url, content=raw, headers={"Content-Type": "application/json", SIGNATURE_HEADER: signature(secret, raw)}
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Delivery failed: {type(exc).__name__}") from exc
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"Delivery failed with status {response.status_code}")
    return response.status_code


def _fail(db: Session, row: WebhookDelivery, error: str) -> None:
    row.attempts += 1
    row.last_error = error[:400]
    if row.attempts >= 1 + len(RETRY_LADDER):
        row.status = "failed"
        row.locked_until = None
    else:
        row.status = "pending"
        row.available_at = now_utc() + timedelta(minutes=RETRY_LADDER[row.attempts - 1])
        row.locked_until = None
    db.commit()


async def process_due(db: Session, *, limit: int = BATCH_LIMIT) -> int:
    """Send due deliveries. One claim per row, retried down the ladder."""
    count = 0
    for _ in range(limit):
        now = now_utc()
        other = aliased(WebhookDelivery)
        row = db.scalar(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.status == "pending",
                WebhookDelivery.available_at <= now,
                or_(WebhookDelivery.locked_until.is_(None), WebhookDelivery.locked_until < now),
                ~select(other.id)
                .where(
                    other.subscription_id == WebhookDelivery.subscription_id,
                    other.id != WebhookDelivery.id,
                    other.status == "processing",
                    other.locked_until > now,
                )
                .exists(),
            )
            .order_by(WebhookDelivery.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not row:
            db.rollback()
            break
        row.status, row.locked_until = "processing", now + timedelta(minutes=5)
        delivery_id = row.id
        try:
            db.commit()
        except Exception:
            db.rollback()
            break
        try:
            subscription = db.get(WebhookSubscription, row.subscription_id)
            integration = subscription.integration if subscription else None
            if not subscription or not subscription.is_active or integration is None or integration.revoked_at is not None:
                db.delete(row)
                db.commit()
                continue
            secret = decrypt_secret(subscription.encrypted_secret)
            code = await _post(subscription.url, secret, _payload(row))
            done = db.get(WebhookDelivery, delivery_id)
            if done is None:
                continue
            done.status, done.response_code = "sent", code
            done.sent_at = now_utc()
            done.last_error = None
            done.locked_until = None
            db.commit()
        except Exception as exc:
            db.rollback()
            row = db.get(WebhookDelivery, delivery_id)
            if row is None:
                continue
            _fail(db, row, str(exc))
            logger.error("Webhook delivery failed for %s (%s)", delivery_id, type(exc).__name__)
        count += 1
    return count


def replay(db: Session, delivery: WebhookDelivery) -> WebhookDelivery:
    """Requeue a log row for immediate delivery, attempts from scratch."""
    delivery.status = "pending"
    delivery.attempts = 0
    delivery.available_at = now_utc()
    delivery.locked_until = None
    delivery.last_error = None
    delivery.response_code = None
    delivery.sent_at = None
    db.commit()
    db.refresh(delivery)
    return delivery
