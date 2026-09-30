"""The platform owner's administration of agencies, their clients and the
audit log. Every route requires the platform identity; the invitation flows
an invitee opens are the only public part and live in
``platform_invitations.py``.
"""

import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_platform_admin
from ..models import Agency, AgencySlugAlias, Agent, Client, PlatformAdmin, PlatformAuditEvent, UsageRecord
from .. import agency_features
from .. import channel_quotas
from ..schemas_platform import (
    PlatformAccessUpdate,
    PlatformAgencyCreated,
    PlatformAgencyCreate,
    PlatformAgencyOut,
    PlatformAgencyUserOut,
    PlatformAgencyUpdate,
    PlatformAuditEventOut,
    PlatformClientOut,
    PlatformFeaturesOut,
    PlatformFeaturesUpdate,
    PlatformInfrastructureClient,
    PlatformInvitationCreate,
    PlatformInvitationIssued,
    PlatformInvitationOut,
    PlatformOverviewOut,
    PlatformUsageOut,
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
        "allocations": {key: value for key, value in channel_quotas.normalize(client.channel_allocations).items() if value is not None},
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


@router.get("/agencies/{agency_id}/users", response_model=list[PlatformAgencyUserOut])
def agency_users(
    agency_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_platform_admin),
):
    """Who is in the agency: its administrators and the people who work there.

    The profile showed how many clients and how many agents an agency has, which
    reads the same whether the account is in daily use or was opened once and
    forgotten. This is the answer to that, and the invitation form on the profile
    only ever re-sent the first administrator's invitation, so nothing else
    showed who was already inside.
    """
    agency = db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    return platform_service.agency_users(db, agency)


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


@router.put("/agencies/{agency_id}/access", response_model=PlatformAgencyOut)
def set_agency_access(
    agency_id: uuid.UUID,
    payload: PlatformAccessUpdate,
    db: Session = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_platform_admin),
):
    """Block or restore an agency's access. Blocking denies every credential
    of the agency at its next request (sessions, tokens, portals, mobile,
    OAuth, invitations); it never touches messaging, data stores or channels.
    Restoring leaves every per-client and per-channel setting untouched."""
    agency = db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    if payload.status == "blocked":
        if agency.access_status != "blocked":
            agency.access_status = "blocked"
            agency.access_blocked_at = datetime.now(timezone.utc)
            agency.access_block_reason = (payload.reason or "").strip()
            platform_service.record_audit(
                db, admin, "agency.blocked", target_agency_id=agency.id,
                resource_type="agency", resource_id=str(agency.id),
                details={"reason": agency.access_block_reason},
            )
    elif agency.access_status != "active":
        agency.access_status = "active"
        agency.access_blocked_at = None
        agency.access_block_reason = ""
        platform_service.record_audit(
            db, admin, "agency.unblocked", target_agency_id=agency.id,
            resource_type="agency", resource_id=str(agency.id),
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
    return [_audit_out(row) for row in rows]


def _audit_out(row: PlatformAuditEvent) -> dict:
    return {
        "id": row.id,
        "actor_name": row.actor_name,
        "action": row.action,
        "target_agency_id": row.target_agency_id,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "details": row.details,
        "created_at": row.created_at,
    }


def _usage_totals(db: Session, agency_id: uuid.UUID | None) -> dict:
    query = select(
        func.count(UsageRecord.id),
        func.coalesce(func.sum(UsageRecord.input_tokens), 0),
        func.coalesce(func.sum(UsageRecord.output_tokens), 0),
        func.sum(UsageRecord.cost_usd),
        func.count(UsageRecord.id).filter(UsageRecord.cost_usd.is_(None)),
    )
    if agency_id is not None:
        query = query.where(UsageRecord.agency_id == agency_id)
    replies, tokens_in, tokens_out, cost, unpriced = db.execute(query).one()
    return {
        "replies": int(replies),
        "input_tokens": int(tokens_in),
        "output_tokens": int(tokens_out),
        "cost_usd": float(cost) if cost is not None else None,
        "unpriced_replies": int(unpriced),
    }


@router.put("/agencies/{agency_id}/features", response_model=PlatformAgencyOut)
def set_agency_features(
    agency_id: uuid.UUID,
    payload: PlatformFeaturesUpdate,
    db: Session = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_platform_admin),
):
    """The module switches, the line quotas and which preset the switches came
    from. A ceiling: it never rewrites what the agency and its clients chose
    below, it only caps what the portal and mobile sessions expose and how many
    lines may be connected."""
    agency = db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    previous = agency_features.normalize(agency.features)
    previous_quotas = {key: value for key, value in channel_quotas.normalize(agency.channel_quotas).items() if value is not None}
    agency.features = agency_features.merged(agency.features, payload.features)
    if payload.plan is not None:
        if payload.plan and payload.plan not in agency_features.PRESETS:
            raise HTTPException(status_code=422, detail=f"Unknown plan: {payload.plan}")
        agency.plan = payload.plan
    if payload.channel_quotas is not None:
        agency.channel_quotas = channel_quotas.merge(agency.channel_quotas, payload.channel_quotas)
    platform_service.record_audit(
        db, admin, "agency.features_changed", target_agency_id=agency.id,
        resource_type="agency", resource_id=str(agency.id),
        details={
            "previous": previous,
            "features": agency_features.normalize(agency.features),
            "plan": agency.plan,
            "previous_channel_quotas": previous_quotas,
            "channel_quotas": {key: value for key, value in channel_quotas.normalize(agency.channel_quotas).items() if value is not None},
        },
    )
    db.commit()
    db.refresh(agency)
    return platform_service.agency_out(db, agency)


@router.get("/features", response_model=PlatformFeaturesOut)
def feature_catalog(
    db: Session = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_platform_admin),
):
    """The module catalogue, the named presets, and the channel types a number
    can be put on."""
    return {
        "catalog": [{"key": key, "default": default} for key, default in agency_features.CATALOG],
        "presets": {name: list(keys) for name, keys in agency_features.PRESETS.items()},
        "quotas": [
            {"key": key, "label": channel_quotas.LABELS.get(key, key), "max": channel_quotas.MAX_QUOTA}
            for key in channel_quotas.CATALOG
        ],
    }


@router.get("/overview", response_model=PlatformOverviewOut)
def overview(db: Session = Depends(get_db), admin: PlatformAdmin = Depends(get_current_platform_admin)):
    agencies = db.scalar(select(func.count()).select_from(Agency)) or 0
    blocked = db.scalar(
        select(func.count()).select_from(Agency).where(Agency.access_status == "blocked")
    ) or 0
    clients = db.scalar(select(func.count()).select_from(Client)) or 0
    agents = db.scalar(select(func.count()).select_from(Agent)) or 0
    recent = db.scalars(
        select(PlatformAuditEvent).order_by(PlatformAuditEvent.created_at.desc()).limit(5)
    ).all()
    return {
        "agencies": int(agencies),
        "blocked_agencies": int(blocked),
        "clients": int(clients),
        "agents": int(agents),
        "usage": _usage_totals(db, None),
        "channels": channel_quotas.installation_summary(db),
        "recent_events": [_audit_out(row) for row in recent],
    }


@router.get("/agencies/{agency_id}/usage", response_model=PlatformUsageOut)
def agency_usage(
    agency_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_platform_admin),
):
    if db.get(Agency, agency_id) is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    agency = db.get(Agency, agency_id)
    day = cast(func.timezone("UTC", UsageRecord.created_at), Date)
    rows = db.execute(
        select(
            day,
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.input_tokens), 0),
            func.coalesce(func.sum(UsageRecord.output_tokens), 0),
            func.sum(UsageRecord.cost_usd),
        )
        .where(UsageRecord.agency_id == agency_id)
        .group_by(day)
        .order_by(day)
    ).all()
    return {
        "total": _usage_totals(db, agency_id),
        "days": [
            {
                "date": row[0].isoformat(),
                "replies": int(row[1]),
                "input_tokens": int(row[2]),
                "output_tokens": int(row[3]),
                "cost_usd": float(row[4]) if row[4] is not None else None,
            }
            for row in rows
        ],
        "channels": channel_quotas.usage(db, agency),
    }


@router.get("/agencies/{agency_id}/infrastructure", response_model=list[PlatformInfrastructureClient])
def agency_infrastructure(
    agency_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_platform_admin),
):
    """Each client's data store and storage, as observed facts. Never the
    DSN, tokens or credentials: those stay encrypted and unreachable."""
    if db.get(Agency, agency_id) is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    clients = db.scalars(select(Client).where(Client.agency_id == agency_id).order_by(Client.created_at)).all()
    result = []
    for client in clients:
        store = client.data_store
        storage = client.storage_connection
        result.append({
            "client_id": client.id,
            "client_name": client.name,
            "portal_slug": client.portal_slug,
            "data_mode": client.data_mode,
            "datastore": {
                "status": store.status,
                "schema_version": store.schema_version,
                "project_name": store.project_name,
                "region": store.region,
                "db_size_bytes": store.db_size_bytes,
                "last_error": store.last_error,
                "last_checked_at": store.last_checked_at,
                "connected_at": store.connected_at,
            } if store is not None else None,
            "storage": {
                "status": storage.status,
                "bucket": storage.bucket,
                "region": storage.region,
                "quota_mb": storage.quota_mb,
                "max_file_mb": storage.max_file_mb,
                "last_error": storage.last_error,
                "last_checked_at": storage.last_checked_at,
                "connected_at": storage.connected_at,
            } if storage is not None else None,
        })
    return result
