"""Agency-side administration of a client's sales pipeline: its stages and
the kanban board. The portal's own routes (app/routers/portal.py) manage the
same rows through the client's own door."""

import uuid

from fastapi import APIRouter, Depends, Response, status
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Client, User
from ..schemas import PipelineBoardOut, PipelineStageCreate, PipelineStageOut, PipelineStageReorder, PipelineStageUpdate
from ..services import pipeline as pipeline_service

router = APIRouter(prefix="/clients/{client_id}/pipeline", tags=["Pipeline"])


def _client(db: Session, user: User, client_id: uuid.UUID) -> Client:
    client = db.scalar(select(Client).where(Client.id == client_id, Client.agency_id == user.agency_id))
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


@router.get("/stages", response_model=list[PipelineStageOut])
def list_stages(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    client = _client(db, user, client_id)
    return [pipeline_service.stage_out(db, stage) for stage in pipeline_service.list_stages(db, client)]


@router.post("/stages", response_model=PipelineStageOut, status_code=status.HTTP_201_CREATED)
def create_stage(client_id: uuid.UUID, payload: PipelineStageCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    client = _client(db, user, client_id)
    return pipeline_service.stage_out(db, pipeline_service.create_stage(db, client, payload))


@router.patch("/stages/{stage_id}", response_model=PipelineStageOut)
def update_stage(client_id: uuid.UUID, stage_id: uuid.UUID, payload: PipelineStageUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    client = _client(db, user, client_id)
    return pipeline_service.stage_out(db, pipeline_service.update_stage(db, client, stage_id, payload))


@router.post("/stages/reorder", response_model=list[PipelineStageOut])
def reorder_stages(client_id: uuid.UUID, payload: PipelineStageReorder, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    client = _client(db, user, client_id)
    return [pipeline_service.stage_out(db, stage) for stage in pipeline_service.reorder_stages(db, client, payload.stage_ids)]


@router.delete("/stages/{stage_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_stage(client_id: uuid.UUID, stage_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    pipeline_service.delete_stage(db, _client(db, user, client_id), stage_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/board", response_model=PipelineBoardOut)
def get_board(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return pipeline_service.board(db, _client(db, user, client_id))
