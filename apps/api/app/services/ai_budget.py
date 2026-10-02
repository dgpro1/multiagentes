"""A daily cap on what a client's AI may spend.

The agency sets an amount in USD per client. When the day's cost reaches it the
client's AI stops answering until the next day in the client's own timezone, or
until a person resumes it. Messages keep arriving and operators can still answer
from the inbox; only the model stops being called, so nothing more is spent.
"""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Client, Conversation, UsageRecord

logger = logging.getLogger(__name__)


def _zone(client: Client) -> ZoneInfo:
    try:
        return ZoneInfo(client.timezone or "UTC")
    except Exception:  # noqa: BLE001 - an unknown zone falls back to UTC
        return ZoneInfo("UTC")


def today(client: Client):
    return datetime.now(_zone(client)).date()


def spent_today(db: Session, client: Client) -> float:
    """What the client's replies cost since local midnight."""
    start = datetime.combine(today(client), datetime.min.time(), tzinfo=_zone(client)).astimezone(timezone.utc)
    total = db.scalar(
        select(func.coalesce(func.sum(UsageRecord.cost_usd), 0))
        .join(Conversation, Conversation.id == UsageRecord.conversation_id)
        .where(Conversation.client_id == client.id, UsageRecord.created_at >= start)
    )
    return float(total or 0)


def enforce(db: Session, client: Client) -> bool:
    """Switch the client's AI off for today when its cost reached the cap. The caller commits."""
    cap = client.ai_daily_cap_usd
    if cap is None or client.ai_paused:
        return False
    if spent_today(db, client) < float(cap):
        return False
    client.ai_paused_on = today(client)
    logger.warning("The AI of client %s reached its daily cap of %s USD and is paused for today", client.id, cap)
    return True
