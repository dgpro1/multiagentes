"""The invitation flows an invitee opens, without any session.

The token travels in the path; only its digest is stored, and the routes
answer with the few fields a join page needs. Acceptance consumes the
invitation, creates the agency's first administrator and opens that person's
agency session.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Agency, AgencyAdminInvitation
from ..ratelimit import login_rate_limit
from ..schemas import UserOut
from ..schemas_platform import PublicInvitationAccept, PublicInvitationInfo
from ..services import platform as platform_service
from ..services.api_credentials import digest
from .auth import _set_session_cookie

router = APIRouter(prefix="/platform/invitations", tags=["Platform invitations"])


def _row(db: Session, token: str) -> AgencyAdminInvitation | None:
    return db.scalar(select(AgencyAdminInvitation).where(AgencyAdminInvitation.token_hash == digest(token)))


@router.get("/{token}", response_model=PublicInvitationInfo, dependencies=[Depends(login_rate_limit)])
def invitation_info(token: str, db: Session = Depends(get_db)):
    invitation = _row(db, token)
    if invitation is None:
        raise HTTPException(status_code=404, detail="This invitation does not exist")
    agency = db.get(Agency, invitation.agency_id)
    if agency is None:
        raise HTTPException(status_code=404, detail="This invitation does not exist")
    info = platform_service.invitation_out(invitation, agency)
    return {
        "agency_name": agency.name,
        "agency_slug": agency.slug,
        "email": invitation.email,
        "name": invitation.name,
        "status": info["status"],
        "expires_at": invitation.expires_at,
    }


@router.post("/{token}/accept", response_model=UserOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(login_rate_limit)])
def accept_invitation(token: str, payload: PublicInvitationAccept, response: Response, db: Session = Depends(get_db)):
    user, _agency = platform_service.accept_invitation(db, token, payload.password, payload.name)
    _set_session_cookie(response, user)
    return user
