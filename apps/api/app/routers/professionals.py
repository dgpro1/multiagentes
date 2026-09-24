"""Professionals, agency side: the people a client books time with and the hours
they work. The client portal manages the same rows through its own routes
(``routers/portal.py``); both hand the client to ``services/professionals.py``.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api_scopes import PROFESSIONALS_MANAGE, PROFESSIONALS_READ
from ..database import get_db
from ..deps import confined_client_id, get_current_user, require
from ..models import Client, User
from ..schemas_professionals import ProfessionalCreate, ProfessionalOut, ProfessionalUpdate
from ..services import professionals as professionals_service

router = APIRouter(tags=["Professionals"])


def _client(db: Session, user: User, client_id: uuid.UUID) -> Client:
    query = select(Client).where(Client.id == client_id, Client.agency_id == user.agency_id)
    # A client's portal admin reaches only its own client; see PortalActor.
    if (only_client := confined_client_id(user)) is not None:
        query = query.where(Client.id == only_client)
    client = db.scalar(query)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


@router.get("/clients/{client_id}/professionals", response_model=list[ProfessionalOut], dependencies=[Depends(require(PROFESSIONALS_READ))])
def client_professionals(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return professionals_service.list_professionals(db, _client(db, user, client_id))


@router.post(
    "/clients/{client_id}/professionals", response_model=ProfessionalOut, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(PROFESSIONALS_MANAGE))],
)
def client_create_professional(
    client_id: uuid.UUID, payload: ProfessionalCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return professionals_service.create_professional(db, _client(db, user, client_id), payload)


@router.patch(
    "/clients/{client_id}/professionals/{professional_id}", response_model=ProfessionalOut,
    dependencies=[Depends(require(PROFESSIONALS_MANAGE))],
)
def client_update_professional(
    client_id: uuid.UUID, professional_id: uuid.UUID, payload: ProfessionalUpdate,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    return professionals_service.update_professional(db, _client(db, user, client_id), professional_id, payload)


@router.delete(
    "/clients/{client_id}/professionals/{professional_id}", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require(PROFESSIONALS_MANAGE))],
)
def client_delete_professional(
    client_id: uuid.UUID, professional_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    professionals_service.delete_professional(db, _client(db, user, client_id), professional_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
