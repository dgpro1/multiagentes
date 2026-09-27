from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from collections import Counter

from ..database import each_database, get_db
from ..deps import get_current_user
from ..models import Agent, Client, Conversation, Message, SocialChannel, UsageRecord, User, WhatsAppChannel, WhatsAppCloudChannel, now_utc
from ..schemas import DashboardMetrics, DashboardOut
from ..services import lead_group


router = APIRouter(prefix="/dashboard", tags=["Inicio"])


@router.get("", response_model=DashboardOut)
def dashboard(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    agency_id = user.agency_id
    clients = db.scalar(select(func.count(Client.id)).where(Client.agency_id == agency_id)) or 0
    active_clients = db.scalar(
        select(func.count(Client.id)).where(Client.agency_id == agency_id, Client.is_active.is_(True))
    ) or 0
    agents = db.scalar(select(func.count(Agent.id)).where(Agent.agency_id == agency_id)) or 0
    active_agents = db.scalar(
        select(func.count(Agent.id)).where(Agent.agency_id == agency_id, Agent.is_active.is_(True))
    ) or 0
    # Conversations may live in a client's own database: count in each, add up.
    conversations = 0
    for _client in each_database(db, agency_id=agency_id):
        conversations += db.scalar(
            select(func.count(Conversation.id)).where(Conversation.agency_id == agency_id, lead_group.is_lead_row())
        ) or 0
    channels = db.scalar(
        select(func.count(WhatsAppChannel.id)).where(WhatsAppChannel.agency_id == agency_id)
    ) or 0
    connected_channels = db.scalar(
        select(func.count(WhatsAppChannel.id)).where(
            WhatsAppChannel.agency_id == agency_id,
            WhatsAppChannel.status == "connected",
        )
    ) or 0
    for model in (WhatsAppCloudChannel, SocialChannel):
        channels += db.scalar(select(func.count(model.id)).where(model.agency_id == agency_id)) or 0
        connected_channels += db.scalar(select(func.count(model.id)).where(
            model.agency_id == agency_id, model.status == "connected", model.is_enabled.is_(True))) or 0
    recent_agents = db.scalars(
        select(Agent).where(Agent.agency_id == agency_id, Agent.deleted_at.is_(None)).order_by(Agent.created_at.desc()).limit(5)
    ).all()
    return {
        "clients": clients,
        "active_clients": active_clients,
        "agents": agents,
        "active_agents": active_agents,
        "conversations": conversations,
        "channels": channels,
        "connected_channels": connected_channels,
        "recent_agents": recent_agents,
    }


@router.get("/metrics", response_model=DashboardMetrics)
def dashboard_metrics(
    days: int = Query(default=14, ge=1, le=365),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    agency_id = user.agency_id
    start_date = (now_utc() - timedelta(days=days - 1)).date()
    since = now_utc() - timedelta(days=days)
    # Leads count once: a conversation merged into another is not one of its own.
    active_case = and_(lead_group.is_lead_row(), or_(Conversation.social_channel_id.is_(None), exists(
        select(Message.id).where(Message.conversation_id == Conversation.id,
            Message.kind == "message", Message.is_historical.is_(False)).correlate(Conversation))))

    # Conversations and messages may live in a client's own database: every
    # count is taken in each database the agency uses and added up; names and
    # usage are central.
    messages = 0
    human_conversations = 0
    by_channel: Counter = Counter()
    counts: Counter = Counter()
    per_agent: Counter = Counter()
    day = func.date(Conversation.created_at)
    for _client in each_database(db, agency_id=agency_id):
        messages += db.scalar(
            select(func.count(Message.id))
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Conversation.agency_id == agency_id, Message.created_at >= since, Message.is_historical.is_(False))
        ) or 0
        human_conversations += db.scalar(
            select(func.count(Conversation.id)).where(
                Conversation.agency_id == agency_id, Conversation.mode == "human", Conversation.created_at >= since, active_case
            )
        ) or 0
        for channel, count in db.execute(
            select(Conversation.channel, func.count(Conversation.id))
            .where(Conversation.agency_id == agency_id, Conversation.created_at >= since, active_case)
            .group_by(Conversation.channel)
        ).all():
            by_channel[channel] += count
        # New conversations per day over the selected window (zero-filled below).
        for d, c in db.execute(
            select(day, func.count(Conversation.id))
            .where(Conversation.agency_id == agency_id, day >= start_date, active_case)
            .group_by(day)
        ).all():
            counts[str(d)] += c
        for agent_id, count in db.execute(
            select(Conversation.agent_id, func.count(Conversation.id))
            .where(Conversation.agency_id == agency_id, Conversation.created_at >= since, active_case)
            .group_by(Conversation.agent_id)
        ).all():
            per_agent[agent_id] += count
    daily_conversations = [
        {"date": (start_date + timedelta(days=i)).isoformat(), "count": counts.get((start_date + timedelta(days=i)).isoformat(), 0)}
        for i in range(days)
    ]
    top_ids = [agent_id for agent_id, _ in per_agent.most_common(5)]
    names = dict(db.execute(select(Agent.id, Agent.name).where(Agent.id.in_(top_ids), Agent.agency_id == agency_id)).tuples().all()) if top_ids else {}
    top_agents = [{"id": aid, "name": names[aid], "conversations": per_agent[aid]} for aid in top_ids if aid in names]

    tokens_in, tokens_out = db.execute(
        select(
            func.coalesce(func.sum(UsageRecord.input_tokens), 0),
            func.coalesce(func.sum(UsageRecord.output_tokens), 0),
        ).where(UsageRecord.agency_id == agency_id, UsageRecord.created_at >= since)
    ).one()
    usage_rows = db.execute(
        select(
            UsageRecord.model,
            func.coalesce(func.sum(UsageRecord.input_tokens), 0),
            func.coalesce(func.sum(UsageRecord.output_tokens), 0),
        )
        .where(UsageRecord.agency_id == agency_id, UsageRecord.created_at >= since)
        .group_by(UsageRecord.model)
        .order_by((func.sum(UsageRecord.input_tokens) + func.sum(UsageRecord.output_tokens)).desc())
        .limit(6)
    ).all()
    usage_by_model = [{"model": model, "input_tokens": input_tokens, "output_tokens": output_tokens} for model, input_tokens, output_tokens in usage_rows]

    return {
        "messages": messages,
        "human_conversations": human_conversations,
        "by_channel": dict(by_channel),
        "daily_conversations": daily_conversations,
        "top_agents": top_agents,
        "tokens_in": int(tokens_in),
        "tokens_out": int(tokens_out),
        "usage_by_model": usage_by_model,
    }
