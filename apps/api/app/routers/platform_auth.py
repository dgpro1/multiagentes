"""Sign-in, sign-out and identity of the platform owner.

The platform session is its own cookie and token type; agency and portal
sessions never resolve here, and this cookie never resolves as one of them
(see app.security). Bearer credentials are not read at all, so an ``ol_`` API
token has no path onto this surface.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_platform_admin
from ..models import PlatformAdmin
from ..ratelimit import login_rate_limit
from ..schemas_platform import PlatformAdminOut, PlatformLoginRequest
from ..security import create_platform_token, verify_password

router = APIRouter(prefix="/platform/auth", tags=["Platform authentication"])

PLATFORM_COOKIE = "platform_access_token"


def _set_session_cookie(response: Response, admin: PlatformAdmin) -> None:
    settings = get_settings()
    response.set_cookie(
        key=PLATFORM_COOKIE,
        value=create_platform_token(str(admin.id), admin.session_version),
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=settings.access_token_minutes * 60,
        path="/",
    )


@router.post("/login", response_model=PlatformAdminOut, dependencies=[Depends(login_rate_limit)])
def platform_login(payload: PlatformLoginRequest, response: Response, db: Session = Depends(get_db)):
    admin = db.scalar(select(PlatformAdmin).where(PlatformAdmin.email == payload.email.lower()))
    if not admin or not admin.is_active or not verify_password(payload.password, admin.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect e-mail or password")
    _set_session_cookie(response, admin)
    return admin


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def platform_logout(response: Response):
    response.delete_cookie(PLATFORM_COOKIE, path="/")


@router.get("/me", response_model=PlatformAdminOut)
def platform_me(admin: PlatformAdmin = Depends(get_current_platform_admin)):
    return admin
