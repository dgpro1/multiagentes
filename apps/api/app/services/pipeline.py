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
from sqlalchemy.orm import Session

from ..models import Client, Conversation, PipelineStage, now_utc
from ..schemas import PipelineStageCreate, PipelineStageUpdate
from .conversation_state import set_pipeline_stage
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
        .where(Conversation.pipeline_stage_id == stage.id, Conversation.status == "open")
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
        .where(Conversation.client_id == client.id, Conversation.status == "open", Conversation.archived_at.is_(None))
        .order_by(Conversation.updated_at.desc())
        .limit(MAX_BOARD_CARDS)
    ).all()
    unassigned_count = sum(1 for c in cards if c.pipeline_stage_id is None)
    return {
        "stages": [stage_out(db, stage) for stage in stages],
        "unassigned_count": unassigned_count,
        "cards": [
            {
                "id": c.id, "title": c.title, "contact_name": c.contact_name, "channel": c.channel,
                "account_label": c.account_label, "mode": c.mode, "status": c.status,
                "pipeline_stage_id": c.pipeline_stage_id, "deal_value": c.deal_value,
                "preview": (c.messages[-1].content[:140] if c.messages else ""), "updated_at": c.updated_at,
            }
            for c in cards
        ],
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
