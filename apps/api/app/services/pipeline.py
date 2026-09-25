"""Sales pipeline: a client's own stages, and the kanban board of open
conversations sitting in them.

Shared by the client portal and the agency's client page, the way teams are;
both routers resolve the client their own way and hand it here. Moving a
conversation between stages is also reachable from inside a chat generation
(app/services/whatsapp_inbound.py), through the built-in tool this module
also builds.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..models import Agent, Client, Contact, Conversation, PipelineStage, now_utc
from ..schemas import PipelineStageCreate, PipelineStageUpdate, QuickLeadCreate
from . import lead_group
from .contacts import normalize_phone, resolve_contact
from .conversation_state import record_activity, set_pipeline_stage
from .tools.specs import ToolSpec

MAX_STAGES = 30


def get_stage(db: Session, client: Client, stage_id: uuid.UUID) -> PipelineStage:
    stage = db.scalar(select(PipelineStage).where(PipelineStage.id == stage_id, PipelineStage.client_id == client.id))
    if not stage:
        raise HTTPException(status_code=404, detail="Pipeline stage not found")
    return stage


def list_stages(db: Session, client: Client) -> list[PipelineStage]:
    return db.scalars(
        select(PipelineStage).where(PipelineStage.client_id == client.id).order_by(PipelineStage.position)
    ).all()


def stage_out(db: Session, stage: PipelineStage) -> dict:
    count, total = db.execute(
        select(func.count(Conversation.id), func.coalesce(func.sum(Conversation.deal_value), 0))
        .where(Conversation.pipeline_stage_id == stage.id, Conversation.status == "open", lead_group.is_lead_row())
    ).one()
    return {
        "id": stage.id, "name": stage.name, "color": stage.color, "position": stage.position,
        "conversation_count": int(count), "deal_value_total": float(total),
    }


def create_stage(db: Session, client: Client, payload: PipelineStageCreate) -> PipelineStage:
    if len(list_stages(db, client)) >= MAX_STAGES:
        raise HTTPException(status_code=409, detail=f"A client can have up to {MAX_STAGES} pipeline stages")
    if db.scalar(select(PipelineStage.id).where(PipelineStage.client_id == client.id, PipelineStage.name == payload.name.strip())):
        raise HTTPException(status_code=409, detail="A stage with this name already exists")
    next_position = db.scalar(select(func.coalesce(func.max(PipelineStage.position), -1))
                               .where(PipelineStage.client_id == client.id)) + 1
    stage = PipelineStage(client_id=client.id, name=payload.name.strip(), color=payload.color, position=next_position)
    db.add(stage)
    db.commit()
    db.refresh(stage)
    return stage


def update_stage(db: Session, client: Client, stage_id: uuid.UUID, payload: PipelineStageUpdate) -> PipelineStage:
    stage = get_stage(db, client, stage_id)
    if payload.name is not None:
        name = payload.name.strip()
        duplicate = db.scalar(select(PipelineStage.id).where(
            PipelineStage.client_id == client.id, PipelineStage.name == name, PipelineStage.id != stage.id))
        if duplicate:
            raise HTTPException(status_code=409, detail="A stage with this name already exists")
        stage.name = name
    if payload.color is not None:
        stage.color = payload.color
    stage.updated_at = now_utc()
    db.commit()
    db.refresh(stage)
    return stage


def reorder_stages(db: Session, client: Client, stage_ids: list[uuid.UUID]) -> list[PipelineStage]:
    stages = {stage.id: stage for stage in list_stages(db, client)}
    if set(stage_ids) != set(stages):
        raise HTTPException(status_code=422, detail="The list must name every stage of this client exactly once")
    for position, stage_id in enumerate(stage_ids):
        stages[stage_id].position = position
    db.commit()
    return list_stages(db, client)


def delete_stage(db: Session, client: Client, stage_id: uuid.UUID) -> None:
    # Conversations keep living; the FK sets their stage to NULL.
    stage = get_stage(db, client, stage_id)
    db.delete(stage)
    db.commit()


MAX_BOARD_CARDS = 400


def board(db: Session, client: Client) -> dict:
    """Every staged deal, plus open conversations still off the pipeline (a
    virtual "not in the pipeline" column the board renders first) so a
    conversation can be dragged in without a separate picker elsewhere."""
    stages = list_stages(db, client)
    cards = db.scalars(
        select(Conversation)
        .options(selectinload(Conversation.contact).selectinload(Contact.tags))
        .where(
            Conversation.client_id == client.id,
            Conversation.status == "open",
            Conversation.archived_at.is_(None),
            lead_group.is_lead_row(),
        )
        .order_by(Conversation.updated_at.desc())
        .limit(MAX_BOARD_CARDS)
    ).all()
    unassigned_count = sum(1 for c in cards if c.pipeline_stage_id is None)
    return {
        "currency": client.currency or "USD",
        "stages": [stage_out(db, stage) for stage in stages],
        "unassigned_count": unassigned_count,
        "cards": [_card_dict(card) for card in cards],
    }


def _card_dict(card: Conversation) -> dict:
    contact = card.contact
    return {
        "id": card.id, "number": card.number, "title": card.title, "contact_name": card.contact_name,
        "contact_id": card.contact_id,
        "tags": [{"name": tag.name, "color": tag.color} for tag in (contact.tags if contact else [])],
        "channel": card.channel,
        "account_label": card.account_label, "mode": card.mode, "status": card.status,
        "pipeline_stage_id": card.pipeline_stage_id, "deal_value": card.deal_value,
        "preview": (card.messages[-1].content[:140] if card.messages else ""), "updated_at": card.updated_at,
    }


def move_conversation(
    db: Session, client: Client, conversation: Conversation, stage_id: uuid.UUID | None,
    deal_value: float | None, *, actor: str | None = None,
) -> Conversation:
    if conversation.client_id != client.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    stage = get_stage(db, client, stage_id) if stage_id else None
    set_pipeline_stage(db, conversation, stage, deal_value=deal_value, actor=actor)
    db.commit()
    db.refresh(conversation)
    return conversation


def create_quick_lead(db: Session, client: Client, payload: QuickLeadCreate, *, actor: str) -> dict:
    """A manually created deal: contact plus an open, human-held case in the
    given stage. There is no channel behind it yet, so nobody is notified
    and the AI never answers it."""
    agent = None
    if payload.agent_id is not None:
        agent = db.scalar(
            select(Agent).where(
                Agent.id == payload.agent_id,
                Agent.client_id == client.id,
                Agent.agency_id == client.agency_id,
                Agent.deleted_at.is_(None),
            )
        )
        if not agent:
            raise HTTPException(status_code=400, detail="Select an agent that belongs to this client")
    else:
        agent = db.scalar(
            select(Agent).where(
                Agent.client_id == client.id,
                Agent.agency_id == client.agency_id,
                Agent.is_active.is_(True),
                Agent.deleted_at.is_(None),
            ).order_by(Agent.created_at).limit(1)
        )
        if not agent:
            raise HTTPException(status_code=400, detail="Add an active agent to this client before creating leads")
    stage = get_stage(db, client, payload.pipeline_stage_id) if payload.pipeline_stage_id else None
    name = payload.contact_name.strip()
    phone = normalize_phone(payload.contact_phone) if payload.contact_phone else None
    if payload.contact_phone and not phone:
        raise HTTPException(status_code=422, detail="The phone number is too short to be valid")
    if phone:
        contact = resolve_contact(db, client.id, phone=phone, name=name)
    else:
        from ..models import Contact as ContactModel

        contact = ContactModel(client_id=client.id, name=name)
        db.add(contact)
        db.flush()
    now = now_utc()
    conversation = Conversation(
        agency_id=client.agency_id,
        client_id=client.id,
        agent_id=agent.id,
        channel="manual",
        mode="human",
        status="open",
        title=name[:240],
        contact_id=contact.id,
        contact_name=name,
        pipeline_stage_id=stage.id if stage else None,
        deal_value=payload.deal_value,
        taken_over_at=now,
    )
    db.add(conversation)
    db.flush()
    record_activity(db, conversation, "started", actor=actor)
    db.commit()
    db.refresh(conversation)
    return _card_dict(conversation)


# The built-in agent tool: the model moves the deal itself as the conversation
# progresses, the way it calls escalate_to_human (app/services/escalation.py).

PIPELINE_PROMPT_INTRO = (
    "PIPELINE DE VENTAS: dispones de la herramienta move_pipeline_stage para mover este negocio a la etapa que "
    "corresponda según cómo avanza la conversación. Etapas disponibles, en orden:\n"
)


def pipeline_enabled(stages: list[PipelineStage]) -> bool:
    return bool(stages)


def pipeline_prompt(stages: list[PipelineStage]) -> str:
    lines = [f"- {stage.name}" for stage in stages]
    return PIPELINE_PROMPT_INTRO + "\n".join(lines)


def build_pipeline_spec(stages: list[PipelineStage], holder: list) -> ToolSpec:
    by_name = {stage.name: stage for stage in stages}

    def handler(args: dict) -> tuple[str, bool]:
        name = (args.get("stage") or "").strip()
        stage = by_name.get(name)
        if not stage:
            return f"Unknown stage. Choose one of: {', '.join(by_name)}", True
        holder.clear()
        holder.append(stage)
        return f"Deal moved to stage \"{stage.name}\".", False

    return ToolSpec(
        name="move_pipeline_stage",
        description="Move this conversation's deal to a different pipeline stage as it progresses.",
        input_schema={
            "type": "object",
            "properties": {"stage": {"type": "string", "enum": list(by_name), "description": "The exact name of the target stage"}},
            "required": ["stage"],
        },
        handler=handler,
    )
