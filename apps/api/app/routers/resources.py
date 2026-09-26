"""Resource library, agency side: the files and links a client's agent may send,
and the client's own R2 bucket where the files live. The client portal manages
the same rows through its own routes; both hand the client to
``services/resources_catalog.py`` and ``services/storage_connection.py``.

The public onboarding link (no session) lets the business owner paste the
credentials they created in their own Cloudflare account.
"""

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api_scopes import RESOURCES_MANAGE, RESOURCES_READ, STORAGE_MANAGE, STORAGE_READ
from ..database import get_db
from ..deps import confined_client_id, get_current_user, require
from ..models import Client, User
from ..ratelimit import storage_connect_rate_limit
from ..schemas_resources import (
    ResourceCreate,
    ResourceOut,
    ResourceUpdate,
    StorageConnect,
    StorageConnectInfoOut,
    StorageConnectionOut,
    StorageLimitsUpdate,
    StorageLinkOut,
)
from ..services import resources_catalog, storage_connection
from ..services.attachments import file_response

router = APIRouter(tags=["Resources"])

# Files are read whole before they are checked, so the request is bounded by the
# ceiling in services/resource_storage.py plus a little slack for the multipart frame.
UPLOAD_READ_LIMIT = 21 * 1024 * 1024


def _client(db: Session, user: User, client_id: uuid.UUID) -> Client:
    query = select(Client).where(Client.id == client_id, Client.agency_id == user.agency_id)
    # A client's portal admin reaches only its own client; see PortalActor.
    if (only_client := confined_client_id(user)) is not None:
        query = query.where(Client.id == only_client)
    client = db.scalar(query)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


async def read_upload(file: UploadFile) -> bytes:
    data = await file.read(UPLOAD_READ_LIMIT + 1)
    if len(data) > UPLOAD_READ_LIMIT:
        raise HTTPException(status_code=413, detail="The file is too large")
    return data


# Resources

@router.get("/clients/{client_id}/resources", response_model=list[ResourceOut], dependencies=[Depends(require(RESOURCES_READ))])
def client_resources(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return resources_catalog.list_resources(db, _client(db, user, client_id))


@router.post(
    "/clients/{client_id}/resources", response_model=ResourceOut, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(RESOURCES_MANAGE))],
)
def client_create_link_resource(
    client_id: uuid.UUID, payload: ResourceCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return resources_catalog.create_link(db, _client(db, user, client_id), payload)


@router.post(
    "/clients/{client_id}/resources/upload", response_model=ResourceOut, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(RESOURCES_MANAGE))],
)
async def client_upload_resource(
    client_id: uuid.UUID,
    name: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    payload = ResourceCreate(kind="file", name=name, description=description)
    data = await read_upload(file)
    return resources_catalog.create_file(
        db, _client(db, user, client_id), payload, data=data, mime=file.content_type or "", filename=file.filename
    )


@router.patch(
    "/clients/{client_id}/resources/{resource_id}", response_model=ResourceOut,
    dependencies=[Depends(require(RESOURCES_MANAGE))],
)
def client_update_resource(
    client_id: uuid.UUID, resource_id: uuid.UUID, payload: ResourceUpdate,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    return resources_catalog.update_resource(db, _client(db, user, client_id), resource_id, payload)


@router.delete(
    "/clients/{client_id}/resources/{resource_id}", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require(RESOURCES_MANAGE))],
)
def client_delete_resource(
    client_id: uuid.UUID, resource_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    resources_catalog.delete_resource(db, _client(db, user, client_id), resource_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/clients/{client_id}/resources/{resource_id}/file", dependencies=[Depends(require(RESOURCES_READ))])
def client_resource_file(
    client_id: uuid.UUID, resource_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    client = _client(db, user, client_id)
    row = resources_catalog.get_resource(db, client, resource_id)
    return file_response(resources_catalog.read_file(client, row), row.mime or "application/octet-stream", row.filename)


# The client's own bucket

@router.get("/clients/{client_id}/storage", response_model=StorageConnectionOut, dependencies=[Depends(require(STORAGE_READ))])
def client_storage(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return storage_connection.out(db, _client(db, user, client_id))


@router.put("/clients/{client_id}/storage", response_model=StorageConnectionOut, dependencies=[Depends(require(STORAGE_MANAGE))])
def client_connect_storage(
    client_id: uuid.UUID, payload: StorageConnect, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    client = _client(db, user, client_id)
    storage_connection.connect(db, client, payload)
    return storage_connection.out(db, client)


@router.post("/clients/{client_id}/storage/check", response_model=StorageConnectionOut, dependencies=[Depends(require(STORAGE_MANAGE))])
def client_check_storage(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    client = _client(db, user, client_id)
    storage_connection.recheck(db, client)
    return storage_connection.out(db, client)


@router.patch("/clients/{client_id}/storage/limits", response_model=StorageConnectionOut, dependencies=[Depends(require(STORAGE_MANAGE))])
def client_storage_limits(
    client_id: uuid.UUID, payload: StorageLimitsUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    client = _client(db, user, client_id)
    storage_connection.update_limits(db, client, payload)
    return storage_connection.out(db, client)


@router.delete("/clients/{client_id}/storage", response_model=StorageConnectionOut, dependencies=[Depends(require(STORAGE_MANAGE))])
def client_disconnect_storage(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    client = _client(db, user, client_id)
    storage_connection.disconnect(db, client)
    return storage_connection.out(db, client)


@router.post("/clients/{client_id}/storage/link", response_model=StorageLinkOut, dependencies=[Depends(require(STORAGE_MANAGE))])
def client_storage_link(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    conn = storage_connection.renew_link(db, _client(db, user, client_id))
    return {"connect_url": storage_connection.connect_url(conn), "connect_expires_at": conn.connect_expires_at}


# The public onboarding link (no session: opened directly by the business owner).

@router.get("/storage/connect/{token}", response_model=StorageConnectInfoOut, dependencies=[Depends(storage_connect_rate_limit)])
def storage_connect_info(token: str, db: Session = Depends(get_db)):
    return storage_connection.link_info(db, token)


@router.post("/storage/connect/{token}", response_model=StorageConnectInfoOut, dependencies=[Depends(storage_connect_rate_limit)])
def storage_connect_by_link(token: str, payload: StorageConnect, db: Session = Depends(get_db)):
    storage_connection.connect_by_link(db, token, payload)
    return storage_connection.link_info(db, token)
