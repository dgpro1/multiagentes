"""The agency's own Supabase project: connect it, check it, disconnect it.

Everything an agency administrator does to look after its clients' data in its
own project. Moving a client in and out is the client's own datastore switch
(``/clients/{id}/datastore/switch``). These routes sit under ``/api/agency``, a
prefix API tokens are refused on: connecting a database is a person's gesture.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas_resources import CloudflareConnect, StorageConnect
from ..services import agency_backend

router = APIRouter(prefix="/agency", tags=["Agency backend"])


class AgencyBackendOut(BaseModel):
    status: Literal["none", "pending", "authorized", "connected", "error"]
    module_enabled: bool
    oauth_ready: bool
    recommended_region: str = ""
    clients_in_agency: int = 0
    project_ref: str = ""
    project_name: str = ""
    region: str = ""
    last_error: str | None = None
    last_checked_at: datetime | None = None
    connected_at: datetime | None = None


class AgencyProjectOut(BaseModel):
    ref: str
    name: str
    region: str
    status: str


class AgencyProjectChoice(BaseModel):
    ref: str = Field(min_length=20, max_length=20)


class AgencyTokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=400)


class AgencyStorageOut(BaseModel):
    status: Literal["none", "pending", "connected", "error"]
    module_enabled: bool
    clients_hosted: int = 0
    account_id: str = ""
    bucket: str = ""
    access_key_hint: str = ""
    last_error: str | None = None
    last_checked_at: datetime | None = None
    connected_at: datetime | None = None


@router.get("/backend", response_model=AgencyBackendOut)
def get_backend(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return agency_backend.out(db, user.agency)


@router.post("/backend/connect")
def start_connect(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return {"authorization_url": agency_backend.start_connection(db, user.agency)}


@router.put("/backend/token", response_model=AgencyBackendOut)
async def connect_token(payload: AgencyTokenIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Connect Supabase with a personal access token instead of OAuth."""
    await agency_backend.connect_token(db, user.agency, payload.token)
    return agency_backend.out(db, user.agency)


@router.get("/backend/projects", response_model=list[AgencyProjectOut])
async def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return await agency_backend.projects(db, user.agency)


@router.post("/backend/project", response_model=AgencyBackendOut)
async def choose_project(payload: AgencyProjectChoice, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    await agency_backend.choose_project(db, user.agency, payload.ref)
    return agency_backend.out(db, user.agency)


@router.post("/backend/check", response_model=AgencyBackendOut)
async def check(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    await agency_backend.recheck(db, user.agency)
    return agency_backend.out(db, user.agency)


@router.delete("/backend", response_model=AgencyBackendOut)
def disconnect(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    agency_backend.disconnect(db, user.agency)
    return agency_backend.out(db, user.agency)


@router.get("/storage", response_model=AgencyStorageOut)
def get_storage(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return agency_backend.storage_out(db, user.agency)


@router.put("/storage", response_model=AgencyStorageOut)
def connect_storage(payload: StorageConnect, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    agency_backend.storage_connect(db, user.agency, payload)
    return agency_backend.storage_out(db, user.agency)


@router.put("/storage/cloudflare", response_model=AgencyStorageOut)
def connect_storage_cloudflare(payload: CloudflareConnect, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    agency_backend.storage_connect_cloudflare(db, user.agency, payload)
    return agency_backend.storage_out(db, user.agency)


@router.post("/storage/check", response_model=AgencyStorageOut)
def check_storage(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    agency_backend.storage_recheck(db, user.agency)
    return agency_backend.storage_out(db, user.agency)


@router.delete("/storage", response_model=AgencyStorageOut)
def disconnect_storage(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    agency_backend.storage_disconnect(db, user.agency)
    return agency_backend.storage_out(db, user.agency)
