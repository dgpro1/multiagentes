"""OAuth 2.0 authorization-code flow for third-party integrations.

An ApiIntegration doubles as an OAuth client: the agency registers redirect
URIs and takes a client secret for it, a person approves scopes on the
consent screen, and the third party exchanges the single-use code for a
short access token plus a rotating refresh token. Grants live in the same
two tables as long-lived tokens, distinguished by kind.

Credential endpoints stay outside the token surface on purpose (see
test_api_scope_coverage.py): codes and secrets are exchanged here, never
used as bearers.
"""

from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..api_scopes import DESCRIPTIONS, known
from ..database import get_db
from ..deps import person_only
from ..models import ApiIntegration, ApiToken, User
from ..ratelimit import RateLimiter
from ..schemas_oauth import (
    OAuthAuthorizeRequest,
    OAuthAuthorizeResponse,
    OAuthClientInfo,
    OAuthRevokeRequest,
    OAuthTokenRequest,
    OAuthTokenResponse,
)
from ..services import api_credentials

router = APIRouter(prefix="/oauth", tags=["OAuth"])

# Credential endpoints are strict: secrets are checked here.
oauth_rate_limit = RateLimiter(30, 60, name="oauth")


def _oauth_error(code: str, description: str, http_status: int = 400) -> HTTPException:
    detail = {"error": code, "error_description": description}
    return HTTPException(status_code=http_status, detail=detail)


def _client_by_public_id(db: Session, client_id: str) -> ApiIntegration | None:
    return db.scalar(select(ApiIntegration).where(ApiIntegration.oauth_client_id == client_id.strip()))


def _check_secret(integration: ApiIntegration, secret: str | None) -> None:
    if not secret or not integration.oauth_client_secret_hash:
        raise _oauth_error("invalid_client", "Unknown client or wrong secret", 401)
    if api_credentials.digest(secret) != integration.oauth_client_secret_hash:
        raise _oauth_error("invalid_client", "Unknown client or wrong secret", 401)


def _valid_redirect(uri: str, registered: list) -> str:
    uri = (uri or "").strip()
    if uri and uri in (registered or []):
        return uri
    raise _oauth_error("invalid_request", "This redirect URL is not registered for the client")


def _requested_scopes(integration: ApiIntegration, scope: str) -> list[str]:
    wanted = sorted({part for part in (scope or "").split() if part})
    unknown = sorted(part for part in wanted if not known(part))
    if unknown:
        raise _oauth_error("invalid_scope", f"Unknown scope: {', '.join(unknown)}")
    allowed = set(integration.scopes or [])
    denied = sorted(part for part in wanted if part not in allowed)
    if denied:
        raise HTTPException(
            status_code=403,
            detail={"error": "invalid_scope", "error_description": f"Not granted to this client: {', '.join(denied)}"},
        )
    return wanted or sorted(allowed)


def _landing(uri: str, params: dict) -> str:
    parts = urlsplit(uri)
    query = urlencode(params)
    merged = f"{parts.query}&{query}" if parts.query else query
    return urlunsplit((parts.scheme, parts.netloc, parts.path, merged, parts.fragment))


@router.get("/client", response_model=OAuthClientInfo)
def oauth_client(
    client_id: str = Query(min_length=1, max_length=40),
    db: Session = Depends(get_db),
    user: User = Depends(person_only),
):
    """What the consent screen shows. A person only ever sees clients of
    their own agency."""
    integration = _client_by_public_id(db, client_id)
    if integration is None or integration.agency_id != user.agency_id:
        raise HTTPException(status_code=404, detail="Unknown OAuth client")
    if integration.revoked_at is not None:
        raise HTTPException(status_code=409, detail="This integration was revoked")
    return OAuthClientInfo(
        client_id=integration.oauth_client_id or "",
        name=integration.name,
        scopes=sorted(integration.scopes or []),
        scope_descriptions={key: DESCRIPTIONS[key] for key in sorted(integration.scopes or []) if key in DESCRIPTIONS},
    )


@router.post("/authorize", response_model=OAuthAuthorizeResponse)
def authorize(
    payload: OAuthAuthorizeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(person_only),
):
    """Approve or deny access. Answers the redirect the third party must
    follow; the code itself is never returned in this body."""
    integration = _client_by_public_id(db, payload.client_id)
    if integration is None or integration.agency_id != user.agency_id:
        raise _oauth_error("invalid_request", "Unknown OAuth client")
    if integration.revoked_at is not None:
        raise _oauth_error("invalid_request", "This integration was revoked")
    redirect_uri = _valid_redirect(payload.redirect_uri, integration.oauth_redirect_uris)
    state = payload.state
    if not payload.approved:
        return OAuthAuthorizeResponse(redirect_to=_landing(redirect_uri, {"error": "access_denied", **({"state": state} if state else {})}))
    try:
        scopes = _requested_scopes(integration, payload.scope)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": "invalid_scope", "error_description": str(exc.detail)}
        if detail.get("error") == "invalid_scope":
            return OAuthAuthorizeResponse(redirect_to=_landing(redirect_uri, {**detail, **({"state": state} if state else {})}))
        raise
    _, raw = api_credentials.issue_auth_code(db, integration, scopes=scopes, redirect_uri=redirect_uri)
    params = {"code": raw, **({"state": state} if state else {})}
    return OAuthAuthorizeResponse(redirect_to=_landing(redirect_uri, params))


def _active_token(db: Session, raw: str, kind: str):
    token = db.scalar(
        select(ApiToken).where(ApiToken.token_hash == api_credentials.digest(raw), ApiToken.kind == kind)
    )
    if token is None or not api_credentials.active(token):
        return None
    return token


@router.post("/token", response_model=OAuthTokenResponse, dependencies=[Depends(oauth_rate_limit)])
def token(payload: OAuthTokenRequest, db: Session = Depends(get_db)):
    """Exchange a code or rotate a refresh token. Speaks plain OAuth errors."""
    if payload.grant_type == "authorization_code":
        return _exchange_code(db, payload)
    if payload.grant_type == "refresh_token":
        return _rotate_refresh(db, payload)
    raise _oauth_error("unsupported_grant_type", "Use authorization_code or refresh_token")


def _exchange_code(db: Session, payload: OAuthTokenRequest) -> OAuthTokenResponse:
    if not payload.code or not payload.redirect_uri:
        raise _oauth_error("invalid_request", "Code and redirect_uri are required")
    integration = _client_by_public_id(db, payload.client_id)
    if integration is None:
        raise _oauth_error("invalid_client", "Unknown client or wrong secret", 401)
    _check_secret(integration, payload.client_secret)
    if integration.revoked_at is not None:
        raise _oauth_error("invalid_grant", "This integration was revoked")
    code = _active_token(db, payload.code, api_credentials.AUTH_CODE)
    if code is None or code.integration_id != integration.id or code.redirect_uri != payload.redirect_uri.strip():
        raise _oauth_error("invalid_grant", "Unknown, expired or already used code")
    db.delete(code)
    db.flush()
    (_, access_raw), (_, refresh_raw) = api_credentials.issue_grant_pair(
        db, integration, scopes=list(code.scopes or []), grant_id=code.grant_id
    )
    return OAuthTokenResponse(
        access_token=access_raw,
        expires_in=api_credentials.ACCESS_MINUTES * 60,
        refresh_token=refresh_raw,
        scope=" ".join(code.scopes or []),
    )


def _rotate_refresh(db: Session, payload: OAuthTokenRequest) -> OAuthTokenResponse:
    if not payload.refresh_token:
        raise _oauth_error("invalid_request", "A refresh token is required")
    integration = _client_by_public_id(db, payload.client_id)
    if integration is None:
        raise _oauth_error("invalid_client", "Unknown client or wrong secret", 401)
    _check_secret(integration, payload.client_secret)
    if integration.revoked_at is not None:
        raise _oauth_error("invalid_grant", "This integration was revoked")
    refresh = _active_token(db, payload.refresh_token, api_credentials.REFRESH)
    if refresh is None or refresh.integration_id != integration.id:
        raise _oauth_error("invalid_grant", "Unknown or expired refresh token")
    grant_id = refresh.grant_id
    scopes = list(refresh.scopes or [])
    db.delete(refresh)
    db.execute(delete(ApiToken).where(ApiToken.grant_id == grant_id, ApiToken.kind == api_credentials.ACCESS))
    db.flush()
    (_, access_raw), (_, refresh_raw) = api_credentials.issue_grant_pair(
        db, integration, scopes=scopes, grant_id=grant_id
    )
    return OAuthTokenResponse(
        access_token=access_raw,
        expires_in=api_credentials.ACCESS_MINUTES * 60,
        refresh_token=refresh_raw,
        scope=" ".join(scopes),
    )


@router.post("/revoke", dependencies=[Depends(oauth_rate_limit)])
def revoke(payload: OAuthRevokeRequest, db: Session = Depends(get_db)):
    """Retire a refresh token (and its grant's access tokens) or one access
    token. Unknown tokens answer 200 either way, per RFC 7009."""
    integration = _client_by_public_id(db, payload.client_id)
    if integration is None:
        raise _oauth_error("invalid_client", "Unknown client or wrong secret", 401)
    _check_secret(integration, payload.client_secret)
    token = db.scalar(select(ApiToken).where(ApiToken.token_hash == api_credentials.digest(payload.token)))
    if token is not None and token.integration_id == integration.id:
        if token.kind == api_credentials.REFRESH and token.grant_id is not None:
            grant = token.grant_id
            db.delete(token)
            db.execute(
                delete(ApiToken).where(ApiToken.grant_id == grant, ApiToken.kind == api_credentials.ACCESS)
            )
        else:
            token.revoked_at = datetime.now(timezone.utc)
        db.commit()
    return Response(status_code=status.HTTP_200_OK)
