"""The platform's domain services: agencies, invitations and the audit log.

Everything here acts on the control plane and only for a ``PlatformAdmin``;
no agency-panel session ever reaches it. Every mutation that must be
explainable later writes its audit row in the same transaction as the change.
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..models import Agency, AgencyAdminInvitation, Agent, Client, PlatformAdmin, PlatformAuditEvent, User
from ..security import hash_password
from ..services import access_policy
from ..services.api_credentials import digest
from ..slugs import slug_free, slugify, unique_agency_slug
from .. import agency_features

INVITATION_PREFIX = "inv_"
INVITATION_DAYS = 7


def record_audit(
    db: Session,
    admin: PlatformAdmin,
    action: str,
    *,
    target_agency_id: uuid.UUID | None = None,
    resource_type: str = "",
    resource_id: str = "",
    details: dict | None = None,
    request_id: str = "",
) -> PlatformAuditEvent:
    row = PlatformAuditEvent(
        actor_id=admin.id,
        actor_name=admin.name,
        action=action,
        target_agency_id=target_agency_id,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
        request_id=request_id,
    )
    db.add(row)
    return row


def agency_out(db: Session, agency: Agency) -> dict:
    client_count = db.scalar(select(func.count()).select_from(Client).where(Client.agency_id == agency.id)) or 0
    agent_count = db.scalar(select(func.count()).select_from(Agent).where(Agent.agency_id == agency.id)) or 0
    return {
        "id": agency.id,
        "slug": agency.slug,
        "name": agency.name,
        "brand_color": agency.brand_color,
        "created_at": agency.created_at,
        "client_count": int(client_count),
        "agent_count": int(agent_count),
        "access_status": agency.access_status,
        "access_blocked_at": agency.access_blocked_at,
        "access_block_reason": agency.access_block_reason,
        "features": agency_features.normalize(agency.features),
        "plan": agency.plan,
    }


def invitation_out(invitation: AgencyAdminInvitation, agency: Agency) -> dict:
    now = datetime.now(timezone.utc)
    if invitation.accepted_at is not None:
        status = "accepted"
    elif invitation.revoked_at is not None:
        status = "revoked"
    elif invitation.expires_at <= now:
        status = "expired"
    else:
        status = "pending"
    return {
        "id": invitation.id,
        "agency_id": invitation.agency_id,
        "agency_name": agency.name,
        "email": invitation.email,
        "name": invitation.name,
        "status": status,
        "expires_at": invitation.expires_at,
        "created_at": invitation.created_at,
    }


def invite_admin(
    db: Session, agency: Agency, email: str, name: str, invited_by: PlatformAdmin | None
) -> tuple[AgencyAdminInvitation, str]:
    """Issue (or re-issue) the invitation for an agency's first administrator.

    Re-issuing revokes the previous open invitations of that agency. The
    plaintext token exists only in this return value; the row keeps its digest.
    """
    now = datetime.now(timezone.utc)
    for row in db.scalars(
        select(AgencyAdminInvitation).where(
            AgencyAdminInvitation.agency_id == agency.id,
            AgencyAdminInvitation.accepted_at.is_(None),
            AgencyAdminInvitation.revoked_at.is_(None),
        )
    ):
        row.revoked_at = now
    raw = f"{INVITATION_PREFIX}{secrets.token_urlsafe(32)}"
    invitation = AgencyAdminInvitation(
        agency_id=agency.id,
        email=email.lower().strip(),
        name=name.strip(),
        token_hash=digest(raw),
        expires_at=now + timedelta(days=INVITATION_DAYS),
        invited_by=invited_by.id if invited_by else None,
    )
    db.add(invitation)
    db.flush()
    return invitation, raw


def find_valid_invitation(db: Session, raw: str) -> AgencyAdminInvitation | None:
    invitation = db.scalar(
        select(AgencyAdminInvitation).where(AgencyAdminInvitation.token_hash == digest(raw))
    )
    if invitation is None or invitation.revoked_at is not None or invitation.accepted_at is not None:
        return None
    if invitation.expires_at <= datetime.now(timezone.utc):
        return None
    return invitation


def create_agency(
    db: Session,
    admin: PlatformAdmin,
    *,
    name: str,
    slug: str | None,
    admin_email: str,
    admin_name: str,
) -> tuple[Agency, AgencyAdminInvitation, str]:
    """One transaction: the agency, its first invitation and the audit row.

    Takes the same exclusive lock the public first-run setup takes, so the
    two creators of the first agency cannot interleave their checks.
    """
    db.execute(text("LOCK TABLE agencies IN EXCLUSIVE MODE"))
    if slug:
        agency = Agency(name=name.strip(), slug=slugify(slug))
        if not slug_free(db, agency.slug):
            raise HTTPException(status_code=409, detail="That identifier is already in use")
    else:
        agency = Agency(name=name.strip(), slug=unique_agency_slug(db, name))
    db.add(agency)
    db.flush()
    invitation, raw = invite_admin(db, agency, admin_email, admin_name, admin)
    record_audit(
        db, admin, "agency.created", target_agency_id=agency.id, resource_type="agency", resource_id=str(agency.id)
    )
    db.commit()
    db.refresh(agency)
    return agency, invitation, raw


def accept_invitation(db: Session, raw: str, password: str, name: str | None = None) -> tuple[User, Agency]:
    """Consume an invitation and create the agency's first administrator.

    The row is locked, so two simultaneous acceptances serialize: the second
    sees ``accepted_at`` set and is refused. The user, the acceptance and the
    audit row commit together.
    """
    invitation = db.scalar(
        select(AgencyAdminInvitation)
        .where(AgencyAdminInvitation.token_hash == digest(raw))
        .with_for_update()
    )
    if invitation is None:
        raise HTTPException(status_code=404, detail="This invitation does not exist")
    if access_policy.blocked(db.get(Agency, invitation.agency_id)):
        raise HTTPException(status_code=403, detail=access_policy.BLOCKED_DETAIL)
    if invitation.revoked_at is not None or invitation.accepted_at is not None:
        raise HTTPException(status_code=410, detail="This invitation has already been used or was revoked")
    if invitation.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="This invitation has expired")
    if db.scalar(select(User.id).where(User.email == invitation.email)):
        raise HTTPException(status_code=409, detail="An account with this e-mail already exists")
    invitation.accepted_at = datetime.now(timezone.utc)
    user = User(
        agency_id=invitation.agency_id,
        name=(name or invitation.name).strip(),
        email=invitation.email,
        password_hash=hash_password(password),
        role="admin",
    )
    db.add(user)
    db.add(
        PlatformAuditEvent(
            actor_id=None,
            actor_name=invitation.email,
            action="invitation.accepted",
            target_agency_id=invitation.agency_id,
            resource_type="invitation",
            resource_id=str(invitation.id),
        )
    )
    db.commit()
    db.refresh(user)
    agency = db.get(Agency, invitation.agency_id)
    return user, agency
