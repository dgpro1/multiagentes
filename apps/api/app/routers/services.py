"""Services, agency side: the catalog of services a client offers. The client portal
manages the same rows through its own routes (``routers/portal.py``); both hand
the client to ``services/services_catalog.py``.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api_scopes import SERVICES_MANAGE, SERVICES_READ
from ..database import get_db
from ..deps import confined_client_id, get_current_user, require
from ..models import Client, User
from ..schemas_services import ServiceCreate, ServiceOut, ServiceUpdate
from ..services import services_catalog

router = APIRouter(tags=["Services"])


def _client(db: Session, user: User, client_id: uuid.UUID) -> Client:
    query = select(Client).where(Client.id == client_id, Client.agency_id == user.agency_id)
    # A client's portal admin reaches only its own client; see PortalActor.
    if (only_client := confined_client_id(user)) is not None:
        query = query.where(Client.id == only_client)
    client = db.scalar(query)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


@router.get("/clients/{client_id}/services", response_model=list[ServiceOut], dependencies=[Depends(require(SERVICES_READ))])
def client_services(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return services_catalog.list_services(db, _client(db, user, client_id))


@router.post(
    "/clients/{client_id}/services", response_model=ServiceOut, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(SERVICES_MANAGE))],
)
def client_create_service(
    client_id: uuid.UUID, payload: ServiceCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return services_catalog.create_service(db, _client(db, user, client_id), payload)


@router.patch(
    "/clients/{client_id}/services/{service_id}", response_model=ServiceOut,
    dependencies=[Depends(require(SERVICES_MANAGE))],
)
def client_update_service(
    client_id: uuid.UUID, service_id: uuid.UUID, payload: ServiceUpdate,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    return services_catalog.update_service(db, _client(db, user, client_id), service_id, payload)


@router.delete(
    "/clients/{client_id}/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require(SERVICES_MANAGE))],
)
def client_delete_service(
    client_id: uuid.UUID, service_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    services_catalog.delete_service(db, _client(db, user, client_id), service_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
