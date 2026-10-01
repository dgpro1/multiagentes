"""The client's own Supabase project: agency-side state and the public link.

The agency's client page shows the state, hands out the connection link,
re-checks and disconnects. Everything that needs the customer's consent (the
Supabase OAuth trip, picking a project, provisioning OpenLivery's role) runs
on the public link, which needs no OpenLivery account, and on Supabase's
OAuth callback.
"""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api_scopes import DATASTORE_MANAGE, DATASTORE_READ
from ..database import get_db, use_client
from ..deps import confined_client_id, get_current_user, require
from ..models import Agency, Client, User
from ..ratelimit import storage_connect_rate_limit
from ..services import data_store

router = APIRouter(tags=["Data store"])


class DataStoreOut(BaseModel):
    status: Literal["none", "pending", "authorized", "connected", "error"]
    oauth_ready: bool
    recommended_region: str = ""
    data_mode: Literal["central", "supabase", "agency", "switching"] = "central"
    # Whether the client may be moved into the agency's own Supabase project:
    # the platform allowed the module and the agency connected its project.
    agency_backend_ready: bool = False
    # The client's schema in the agency's project, once it has one. ``retired_at``
    # is set while the client is elsewhere and the schema is only a safety copy.
    agency_schema_status: Literal["none", "pending", "connected", "error"] = "none"
    agency_schema_version: str = ""
    agency_schema_retired_at: datetime | None = None
    agency_schema_last_error: str | None = None
    schema_version: str = ""
    schema_head: str = ""
    project_ref: str = ""
    project_name: str = ""
    region: str = ""
    db_size_bytes: int | None = None
    last_error: str | None = None
    last_checked_at: datetime | None = None
    connected_at: datetime | None = None
    link_active: bool = False


class DataStoreLinkOut(BaseModel):
    connect_url: str
    connect_expires_at: datetime


class DataStoreConnectInfoOut(BaseModel):
    client_name: str
    agency_name: str
    status: Literal["pending", "authorized", "connected", "error"]
    oauth_ready: bool
    recommended_region: str = ""
    project_name: str = ""
    project_ref: str = ""
    last_error: str | None = None
    expires_at: datetime


class SupabaseProjectOut(BaseModel):
    ref: str
    name: str
    region: str
    status: str


class SwitchRequest(BaseModel):
    target: Literal["supabase", "agency", "central"]


class SwitchOut(DataStoreOut):
    counts: dict[str, int] = {}


class ProjectChoice(BaseModel):
    ref: str = Field(min_length=20, max_length=20)


def _out(db: Session, client: Client) -> dict:
    from ..services import agency_backend

    agency = db.get(Agency, client.agency_id)
    placed = client.agency_schema
    return {
        **data_store.out(client),
        "agency_backend_ready": agency_backend.ready(db, agency),
        "agency_schema_status": placed.status if placed else "none",
        "agency_schema_version": placed.schema_version if placed else "",
        "agency_schema_retired_at": placed.retired_at if placed else None,
        "agency_schema_last_error": placed.last_error if placed else None,
    }


def _client(db: Session, user: User, client_id: uuid.UUID) -> Client:
    query = select(Client).where(Client.id == client_id, Client.agency_id == user.agency_id)
    if (only_client := confined_client_id(user)) is not None:
        query = query.where(Client.id == only_client)
    client = db.scalar(query)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    use_client(db, client)
    return client


@router.get("/clients/{client_id}/datastore", response_model=DataStoreOut, dependencies=[Depends(require(DATASTORE_READ))])
def client_data_store(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _out(db, _client(db, user, client_id))


@router.post("/clients/{client_id}/datastore/link", response_model=DataStoreLinkOut, dependencies=[Depends(require(DATASTORE_MANAGE))])
def client_data_store_link(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    store = data_store.renew_link(db, _client(db, user, client_id))
    return {"connect_url": data_store.connect_url(store), "connect_expires_at": store.connect_expires_at}


@router.post("/clients/{client_id}/datastore/check", response_model=DataStoreOut, dependencies=[Depends(require(DATASTORE_MANAGE))])
async def client_data_store_check(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    client = _client(db, user, client_id)
    await data_store.recheck(db, client)
    return _out(db, client)


@router.post("/clients/{client_id}/datastore/schema", response_model=DataStoreOut, dependencies=[Depends(require(DATASTORE_MANAGE))])
def client_data_store_schema(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Create or update the tables in the client's own database. Nothing is copied and nothing switches."""
    from ..services import tenant_schema

    client = _client(db, user, client_id)
    tenant_schema.upgrade(db, client)
    return _out(db, client)


@router.post("/clients/{client_id}/datastore/switch", response_model=SwitchOut, dependencies=[Depends(require(DATASTORE_MANAGE))])
async def client_data_store_switch(
    client_id: uuid.UUID, payload: SwitchRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Move the client's data to its own database, to its agency's project, or
    back to the central one, from wherever it is now. The client's channels
    pause for the few seconds the verified copy takes; what arrives meanwhile
    is kept and processed right after."""
    from ..services import agency_backend, tenant_switch

    client = _client(db, user, client_id)
    if payload.target == "agency":
        # Leaving the agency's project is always allowed; entering it needs the
        # module and the agency's connection.
        agency_backend.ensure_module(user.agency)
    result = await tenant_switch.move(db, client, payload.target)
    db.refresh(client)
    return {**_out(db, client), "counts": result["counts"]}


@router.delete("/clients/{client_id}/datastore", response_model=DataStoreOut, dependencies=[Depends(require(DATASTORE_MANAGE))])
def client_data_store_disconnect(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    client = _client(db, user, client_id)
    data_store.disconnect(db, client)
    return _out(db, client)


# The public link (no session: opened by the business owner) and Supabase's OAuth callback.

@router.get("/datastore/connect/{token}", response_model=DataStoreConnectInfoOut, dependencies=[Depends(storage_connect_rate_limit)])
def data_store_connect_info(token: str, db: Session = Depends(get_db)):
    return data_store.link_info(db, token)


@router.post("/datastore/connect/{token}/start", dependencies=[Depends(storage_connect_rate_limit)])
def data_store_connect_start(token: str, db: Session = Depends(get_db)):
    return {"authorization_url": data_store.start_connection(db, token)}


@router.get("/datastore/connect/{token}/projects", response_model=list[SupabaseProjectOut], dependencies=[Depends(storage_connect_rate_limit)])
async def data_store_connect_projects(token: str, db: Session = Depends(get_db)):
    return await data_store.projects_by_link(db, token)


@router.post("/datastore/connect/{token}/project", response_model=DataStoreConnectInfoOut, dependencies=[Depends(storage_connect_rate_limit)])
async def data_store_connect_project(token: str, payload: ProjectChoice, db: Session = Depends(get_db)):
    await data_store.choose_project(db, token, payload.ref)
    return data_store.link_info(db, token)


@router.get("/supabase/oauth/callback")
async def supabase_oauth_callback(
    state: str = Query(max_length=256), code: str | None = Query(default=None, max_length=8192),
    error: str | None = Query(default=None, max_length=256), db: Session = Depends(get_db),
):
    return RedirectResponse(await data_store.finish_connection(db, state, code, error))
