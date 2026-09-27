"""The platform owner's administration of agencies, their clients and the
audit log. Every route requires the platform identity; the invitation flows
an invitee opens are the only public part and live in
``platform_invitations.py``.
"""

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_platform_admin
from ..models import Agency, AgencySlugAlias, Agent, Client, PlatformAdmin, PlatformAuditEvent
from ..schemas_platform import (
    PlatformAgencyCreated,
    PlatformAgencyCreate,
    PlatformAgencyOut,
    PlatformAgencyUpdate,
    PlatformAuditEventOut,
    PlatformClientOut,
    PlatformInvitationCreate,
    PlatformInvitationIssued,
    PlatformInvitationOut,
)
from ..services import platform as platform_service
from ..slugs import slug_free, slugify

router = APIRouter(prefix="/platform", tags=["Platform administration"])

MAX_LIMIT = 250


def _resolve_agency(db: Session, slug_or_id: str) -> Agency:
    """A page address: the current slug, a retired slug, or the UUID. Every
    hit answers with the canonical agency so links can converge on the
    current address."""
    agency = db.scalar(select(Agency).where(Agency.slug == slug_or_id))
    if agency is None:
        alias = db.scalar(select(AgencySlugAlias).where(AgencySlugAlias.slug == slug_or_id))
        if alias is not None:
            agency = db.get(Agency, alias.agency_id)
    if agency is None:
        try:
            agency = db.get(Agency, uuid.UUID(slug_or_id))
        except ValueError:
            agency = None
    if agency is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    return agency


def _issued(db: Session, invitation, raw: str, agency: Agency) -> dict:
    out = platform_service.invitation_out(invitation, agency)
    out["token"] = raw
    out["url"] = f"{get_settings().frontend_url}/join/{raw}"
    return out


def _client_out(db: Session, client: Client) -> dict:
    agent_count = db.scalar(select(func.count()).select_from(Agent).where(Agent.client_id == client.id)) or 0
    return {
        "id": client.id,
        "name": client.name,
        "portal_slug": client.portal_slug,
        "is_active": client.is_active,
        "data_mode": client.data_mode,
        "agent_count": int(agent_count),
        "created_at": client.created_at,
    }


@router.get("/agencies", response_model=list[PlatformAgencyOut])
def list_agencies(
    response: Response,
    q: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    db: Session = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_platform_admin),
):
    base = select(Agency.id)
    if q.strip():
        base = base.where(Agency.name.ilike(f"%{q.strip()}%"))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    agencies = db.scalars(
        select(Agency).where(Agency.id.in_(base.order_by(Agency.created_at.desc()).offset((page - 1) * limit).limit(limit)))
        .order_by(Agency.created_at.desc())
    ).all()
    response.headers["X-Total-Count"] = str(total)
    return [platform_service.agency_out(db, agency) for agency in agencies]


@router.post("/agencies", response_model=PlatformAgencyCreated, status_code=status.HTTP_201_CREATED)
def create_agency(
    payload: PlatformAgencyCreate,
    db: Session = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_platform_admin),
):
    agency, invitation, raw = platform_service.create_agency(
        db, admin, name=payload.name, slug=payload.slug, admin_email=str(payload.admin_email), admin_name=payload.admin_name
    )
    return {
        "agency": platform_service.agency_out(db, agency),
        "invitation": _issued(db, invitation, raw, agency),
    }


@router.get("/agencies/by-slug/{slug_or_id}", response_model=PlatformAgencyOut)
def resolve_agency(
    slug_or_id: str,
    db: Session = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_platform_admin),
):
    return platform_service.agency_out(db, _resolve_agency(db, slug_or_id))


@router.get("/agencies/{agency_id}", response_model=PlatformAgencyOut)
def get_agency(
    agency_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_platform_admin),
):
    agency = db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    return platform_service.agency_out(db, agency)


@router.patch("/agencies/{agency_id}", response_model=PlatformAgencyOut)
def update_agency(
    agency_id: uuid.UUID,
    payload: PlatformAgencyUpdate,
    db: Session = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_platform_admin),
):
    agency = db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    values = payload.model_dump(exclude_unset=True)
    if "slug" in values and values["slug"]:
        candidate = slugify(values["slug"])
        if candidate != agency.slug:
            if not slug_free(db, candidate):
                raise HTTPException(status_code=409, detail="That identifier is already in use")
            platform_service.record_audit(
                db, admin, "agency.slug_changed", target_agency_id=agency.id,
                resource_type="agency", resource_id=str(agency.id),
                details={"previous_slug": agency.slug, "slug": candidate},
            )
            db.add(AgencySlugAlias(slug=agency.slug, agency_id=agency.id))
        values["slug"] = candidate
    if "brand_color" in values and not re.fullmatch(r"#[0-9a-fA-F]{6}", values["brand_color"] or ""):
        raise HTTPException(status_code=400, detail="The color must use the #075985 format")
    for key, value in values.items():
        setattr(agency, key, value)
    platform_service.record_audit(
        db, admin, "agency.updated", target_agency_id=agency.id,
        resource_type="agency", resource_id=str(agency.id), details={"fields": sorted(values)},
    )
    db.commit()
    db.refresh(agency)
    return platform_service.agency_out(db, agency)


@router.get("/agencies/{agency_id}/clients", response_model=list[PlatformClientOut])
def agency_clients(
    agency_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_platform_admin),
):
    if db.get(Agency, agency_id) is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    clients = db.scalars(
        select(Client).where(Client.agency_id == agency_id).order_by(Client.created_at.desc())
    ).all()
    return [_client_out(db, client) for client in clients]


@router.get("/agencies/{agency_id}/clients/{client_id}", response_model=PlatformClientOut)
def agency_client(
    agency_id: uuid.UUID,
    client_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_platform_admin),
):
    client = db.scalar(select(Client).where(Client.id == client_id, Client.agency_id == agency_id))
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return _client_out(db, client)


@router.post("/agencies/{agency_id}/admin-invitations", response_model=PlatformInvitationIssued, status_code=status.HTTP_201_CREATED)
def reissue_invitation(
    agency_id: uuid.UUID,
    payload: PlatformInvitationCreate,
    db: Session = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_platform_admin),
):
    agency = db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    invitation, raw = platform_service.invite_admin(db, agency, str(payload.email), payload.name, admin)
    platform_service.record_audit(
        db, admin, "invitation.issued", target_agency_id=agency.id,
        resource_type="invitation", resource_id=str(invitation.id),
    )
    db.commit()
    return _issued(db, invitation, raw, agency)


@router.get("/audit-events", response_model=list[PlatformAuditEventOut])
def audit_events(
    response: Response,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    db: Session = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_platform_admin),
):
    total = db.scalar(select(func.count()).select_from(PlatformAuditEvent)) or 0
    rows = db.scalars(
        select(PlatformAuditEvent).order_by(PlatformAuditEvent.created_at.desc()).offset((page - 1) * limit).limit(limit)
    ).all()
    response.headers["X-Total-Count"] = str(total)
    return [
        {
            "id": row.id,
            "actor_name": row.actor_name,
            "action": row.action,
            "target_agency_id": row.target_agency_id,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "details": row.details,
            "created_at": row.created_at,
        }
        for row in rows
    ]
