"""Operational reports: how the agency's agents attended conversations.

The cost side lives in ``routers/reports.py`` (what replies cost). This is
the other half of the reports page: conversations, contacts, how many the AI
resolved, timing and message volume, rolled up and broken down by client and
channel. Everything here reads core tables (``conversations``, ``messages``,
``agents``, ``contacts``, ``clients``); nothing external.

Written as raw SQL because the metrics lean on a lateral join that reads
poorly through the ORM. A client may keep its conversations in its own
database, so the report reads every database the agency uses (see
``database.each_database``): each gives its conversation rows and grouped
message counts, and the totals, breakdowns and medians are computed here.
Nothing joins a client's tables with central ones: agents (for the model and
provider filters) and client names are resolved centrally.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import statistics
from collections import defaultdict

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..database import each_database
from ..models import Agent, Client, Conversation

BUCKETS = {"day", "week", "month", "year"}
MAX_RANGE_DAYS = 366


def safe_bucket(value: str | None) -> str:
    return value if value in BUCKETS else "day"


def safe_tz(tz: str | None) -> str:
    if not tz:
        return "UTC"
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC"
    return tz


class ConversationFilters:
    """Range and scope for conversation-level reports. The range applies to
    when the conversation started; client, agent and channel narrow it."""

    def __init__(self, agency_id: uuid.UUID, *, date_from: date | None, date_to: date | None,
                 client_id: uuid.UUID | None, agent_id: uuid.UUID | None, channel: str | None,
                 tz: str | None, model: str | None = None, provider: str | None = None, bucket: str | None = None) -> None:
        if date_from and date_to:
            if date_to < date_from:
                date_from, date_to = date_to, date_from
            if (date_to - date_from).days > MAX_RANGE_DAYS:
                date_from = date_to - timedelta(days=MAX_RANGE_DAYS)
        self.agency_id = agency_id
        self.date_from = date_from
        self.date_to = date_to
        self.client_id = client_id
        self.agent_id = agent_id
        self.channel = (channel or "").strip()[:40] or None
        self.tz = safe_tz(tz)
        self.model = (model or "").strip()[:180] or None
        self.provider = (provider or "").strip()[:30] or None
        self.bucket = safe_bucket(bucket)
        # The agents matching the model/provider filters, set by operations().
        self.agent_ids: list | None = None

    def where(self, alias: str = "c", stamp: str | None = None, *, leads_only: bool = False) -> tuple[str, dict]:
        """``leads_only`` counts leads: a conversation merged into another one
        is a thread of that lead, not one of its own. Message volume keeps
        counting by thread."""
        stamp = stamp or f"{alias}.created_at"
        clauses = [f"{alias}.agency_id = :agency_id"]
        if leads_only:
            clauses.append(f"{alias}.primary_conversation_id IS NULL")
        params: dict = {"agency_id": self.agency_id, "tz": self.tz, "bucket": self.bucket}
        start = datetime.combine(self.date_from, time.min, tzinfo=timezone.utc) if self.date_from else datetime(1970, 1, 1, tzinfo=timezone.utc)
        params["date_from"] = start
        clauses.append(f"{stamp} >= :date_from")
        if self.date_to:
            clauses.append(f"{stamp} < :date_to")
            params["date_to"] = datetime.combine(self.date_to + timedelta(days=1), time.min, tzinfo=timezone.utc)
        if self.client_id:
            clauses.append(f"{alias}.client_id = :client_id")
            params["client_id"] = self.client_id
        if self.agent_id:
            clauses.append(f"{alias}.agent_id = :agent_id")
            params["agent_id"] = self.agent_id
        if self.channel:
            clauses.append(f"{alias}.channel = :channel")
            params["channel"] = self.channel
        if self.agent_ids is not None:
            # Model and provider are the agent's, resolved centrally (see operations()).
            clauses.append(f"{alias}.agent_id = ANY(:agent_ids)")
            params["agent_ids"] = self.agent_ids
        return " AND ".join(clauses), params


# One row per conversation: the medians need the values, not a per-database median.
_CONV_ROWS = """
SELECT c.client_id, c.channel, c.contact_id, ct.created_at AS contact_created_at,
       c.created_at, c.first_reply_at, c.resolved_at, c.taken_over_at, hr.first_human_at,
       date_trunc(:bucket, c.created_at AT TIME ZONE :tz)::date AS day
FROM conversations c
LEFT JOIN contacts ct ON ct.id = c.contact_id
LEFT JOIN LATERAL (
    SELECT MIN(m.created_at) AS first_human_at FROM messages m
    WHERE m.conversation_id = c.id AND m.sender_type = 'human' AND m.kind = 'message' AND m.created_at >= c.taken_over_at
) hr ON c.taken_over_at IS NOT NULL
"""

# Message counts add up across databases, so they are grouped in SQL.
_MSG_GROUPS = """
SELECT c.client_id, c.channel, date_trunc(:bucket, m.created_at AT TIME ZONE :tz)::date AS day,
    COUNT(*) FILTER (WHERE m.sender_type = 'visitor' AND m.kind = 'message') AS inbound,
    COUNT(*) FILTER (WHERE m.sender_type = 'ai' AND m.kind = 'message') AS ai_replies,
    COUNT(*) FILTER (WHERE m.sender_type = 'human' AND m.kind = 'message') AS human_replies,
    COUNT(*) FILTER (WHERE m.sender_type IN ('ai', 'human')) AS outbound,
    COUNT(*) FILTER (WHERE m.delivery_status = 'failed') AS delivery_failures,
    COALESCE(SUM((
        SELECT COUNT(*) FROM jsonb_array_elements(
            CASE WHEN jsonb_typeof(m.tool_calls::jsonb) = 'array' THEN m.tool_calls::jsonb ELSE '[]'::jsonb END
        ) e WHERE COALESCE((e->>'is_error')::boolean, false)
    )), 0) AS tool_errors
FROM messages m JOIN conversations c ON c.id = m.conversation_id
"""

_MSG_KEYS = ("inbound", "ai_replies", "human_replies", "delivery_failures", "tool_errors")


def _seconds(later, earlier) -> float | None:
    return (later - earlier).total_seconds() if later is not None and earlier is not None else None


def _median(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return float(statistics.median(values)) if values else None


def _conv_metrics(rows: list, date_from) -> dict:
    conversations = len(rows)
    ai_resolved = sum(1 for r in rows if r["resolved_at"] is not None and r["taken_over_at"] is None)
    return {
        "conversations": conversations,
        "contacts": len({r["contact_id"] for r in rows if r["contact_id"] is not None}),
        "new_contacts": len({r["contact_id"] for r in rows if r["contact_id"] is not None
                             and r["contact_created_at"] is not None and r["contact_created_at"] >= date_from}),
        "ai_resolved": ai_resolved,
        "ai_resolved_pct": round(100 * ai_resolved / conversations, 1) if conversations else 0.0,
        "handoffs": sum(1 for r in rows if r["taken_over_at"] is not None),
        "open": sum(1 for r in rows if r["resolved_at"] is None),
        "unanswered": sum(1 for r in rows if r["first_reply_at"] is None),
        "first_reply_s": _median([_seconds(r["first_reply_at"], r["created_at"]) for r in rows]),
        "resolution_s": _median([_seconds(r["resolved_at"], r["created_at"]) for r in rows]),
        "human_wait_s": _median([_seconds(r["first_human_at"], r["taken_over_at"]) for r in rows]),
    }


def _empty_msgs() -> dict:
    return {key: 0 for key in _MSG_KEYS}


def _add_msgs(target: dict, row) -> None:
    for key in _MSG_KEYS:
        target[key] += int(row[key] or 0)


def operations(db: Session, filters: ConversationFilters) -> dict:
    """Totals, breakdowns and timing for the conversations that started in
    the range. Message counts cover messages sent in the range, whichever
    conversation they belong to, so a long-running chat still counts."""
    if filters.model or filters.provider:
        query = select(Agent.id).where(Agent.agency_id == filters.agency_id)
        if filters.model:
            query = query.where(Agent.model == filters.model)
        if filters.provider:
            query = query.where(Agent.provider == filters.provider)
        filters.agent_ids = list(db.scalars(query))
    where, params = filters.where("c", leads_only=True)
    msg_where, msg_params = filters.where("c", stamp="m.created_at")
    msg_params["date_from"] = params["date_from"]

    conv_rows: list = []
    msg_rows: list = []
    for _client in each_database(db, agency_id=filters.agency_id, client_id=filters.client_id):
        # Text SQL names no mapper; ask for the connection the conversations live on.
        conn = db.connection(bind_arguments={"mapper": Conversation.__mapper__})
        conv_rows += [dict(r) for r in conn.execute(text(f"{_CONV_ROWS} WHERE {where}"), params).mappings()]
        msg_rows += [dict(r) for r in conn.execute(
            text(f"{_MSG_GROUPS} WHERE m.kind = 'message' AND {msg_where} GROUP BY 1, 2, 3"), msg_params
        ).mappings()]

    date_from = params["date_from"]
    by_client: dict = defaultdict(list)
    by_channel: dict = defaultdict(list)
    for row in conv_rows:
        by_client[row["client_id"]].append(row)
        by_channel[row["channel"]].append(row)
    msgs_total = _empty_msgs()
    msgs_by_client: dict = defaultdict(_empty_msgs)
    msgs_by_channel: dict = defaultdict(_empty_msgs)
    for row in msg_rows:
        _add_msgs(msgs_total, row)
        _add_msgs(msgs_by_client[row["client_id"]], row)
        _add_msgs(msgs_by_channel[row["channel"]], row)

    days: dict[str, dict] = {}

    def day_entry(day) -> dict:
        return days.setdefault(day.isoformat(), {"day": day.isoformat(), "conversations": 0, "handoffs": 0, "inbound": 0, "outbound": 0})

    for row in conv_rows:
        entry = day_entry(row["day"])
        entry["conversations"] += 1
        entry["handoffs"] += 1 if row["taken_over_at"] is not None else 0
    for row in msg_rows:
        entry = day_entry(row["day"])
        entry["inbound"] += int(row["inbound"] or 0)
        entry["outbound"] += int(row["outbound"] or 0)

    names = dict(db.execute(select(Client.id, Client.name).where(Client.agency_id == filters.agency_id)).tuples().all())
    return {
        "tz": filters.tz,
        "bucket": filters.bucket,
        "totals": {**_conv_metrics(conv_rows, date_from), **msgs_total},
        "by_client": sorted(
            [{"id": str(client_id) if client_id else None, "name": names.get(client_id, ""),
              **_conv_metrics(rows, date_from), **msgs_by_client.get(client_id, _empty_msgs())}
             for client_id, rows in by_client.items()],
            key=lambda entry: -entry["conversations"],
        ),
        "by_channel": sorted(
            [{"id": channel, "name": channel or "", **_conv_metrics(rows, date_from), **msgs_by_channel.get(channel, _empty_msgs())}
             for channel, rows in by_channel.items()],
            key=lambda entry: -entry["conversations"],
        ),
        "by_period": [days[key] for key in sorted(days)],
    }


def _rows(db: Session, sql: str, params: dict):
    """Central text queries (clients, agents, usage records)."""
    return db.execute(text(sql), params).mappings().all()


def filter_options(db: Session, agency_id: uuid.UUID) -> dict:
    """The clients, agents, channels and models the agency has, for the report
    filter dropdowns."""
    clients = _rows(db, "SELECT id, name FROM clients WHERE agency_id = :a ORDER BY name", {"a": agency_id})
    agents = _rows(db, "SELECT id, name, client_id FROM agents WHERE agency_id = :a AND deleted_at IS NULL ORDER BY name", {"a": agency_id})
    found: set[str] = set()
    for _client in each_database(db, agency_id=agency_id):
        conn = db.connection(bind_arguments={"mapper": Conversation.__mapper__})
        found |= {row[0] for row in conn.execute(
            text("SELECT DISTINCT channel FROM conversations WHERE agency_id = :a AND channel <> ''"), {"a": agency_id})}
    channels = [{"channel": channel} for channel in sorted(found)]
    models = _rows(db, "SELECT DISTINCT model FROM usage_records WHERE agency_id = :a AND model <> '' ORDER BY model", {"a": agency_id})
    return {
        "clients": [{"id": str(row["id"]), "name": row["name"]} for row in clients],
        "agents": [{"id": str(row["id"]), "name": row["name"], "client_id": str(row["client_id"])} for row in agents],
        "channels": [row["channel"] for row in channels],
        "models": [row["model"] for row in models],
    }
