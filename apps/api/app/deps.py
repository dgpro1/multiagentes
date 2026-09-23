"""Who is behind the request.

A cookie session and an API token reach the same routes. ``get_current_user``
answers with a ``User`` for a person — exactly as before tokens existed — and
with a ``Principal`` for an API integration, so every route that only reads
``agency_id``, ``id``, ``name`` or ``role`` keeps working untouched.

A token sees one agency, may be confined to a single client of it, holds a set
of scopes, and is refused where a person is required.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import Agency, ApiToken, User
from .ratelimit import api_token_rate_limit
from .security import decode_access_token
from .services.api_credentials import digest

# A busy token would otherwise write its own timestamp on every request.
TOUCH_AFTER_SECONDS = 60


@dataclass
class Principal:
    """An API integration acting for an agency.

    It exposes the attributes routes already read off a ``User``. ``id`` stays
    a real user id — the person who created the integration — because the
    columns that record who did something are foreign keys to ``users``; the
    lines written into a thread name the integration instead (``name``), which
    is what a person reading it cares about.
    """

    agency_id: uuid.UUID
    id: uuid.UUID
    name: str
    role: str = "api"
    email: str = ""
    # Set when the integration is confined to one client; None means the agency.
    client_id: uuid.UUID | None = None
    scopes: frozenset[str] = frozenset()
    integration_id: uuid.UUID | None = None
    token_id: uuid.UUID | None = None
    is_api_token: bool = True
    db: Session | None = field(default=None, repr=False)
    _agency: Agency | None = field(default=None, repr=False)

    @property
    def agency(self) -> Agency:
        """The agency it acts for, loaded once from the session."""
        if self._agency is None:
            if self.db is None:
                raise RuntimeError("This principal has no session to load its agency from")
            self._agency = self.db.get(Agency, self.agency_id)
        return self._agency


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _session_user(access_token: str | None, db: Session) -> User:
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="You are not signed in")
    user_id = decode_access_token(access_token)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="The session expired")
    try:
        parsed_id = uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session") from exc
    user = db.get(User, parsed_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def _bearer(authorization: str | None) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _declared_scopes(route) -> tuple[str, ...] | None:
    """The scopes the matched route declares, or None when it declares none.

    ``require`` marks the dependency it returns, and the route FastAPI matched
    carries it in its dependency tree. A route with no such marker stays closed
    to API tokens, so the surface a token reaches grows on purpose instead of
    by accident.
    """
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return None
    stack = [dependant]
    visited = 0
    while stack and visited < 200:
        node = stack.pop()
        visited += 1
        call = getattr(node, "call", None)
        scopes = getattr(call, "api_scopes", None) if call is not None else None
        if scopes is not None:
            return tuple(scopes)
        stack.extend(getattr(node, "dependencies", ()) or ())
    return None


def _confine_to_client(principal: Principal, request: Request) -> None:
    """A client-limited token reaches only the client it was issued for.

    The public resources will eventually carry the client in every path they
    touch. Until then this is deliberately a denial: a token for one client may
    not act on anything that does not name that client, rather than reach
    another client's data through a route that only filters by agency.
    """
    if principal.client_id is None:
        return
    named = request.path_params.get("client_id")
    if named is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This API token is limited to one client and this route is not",
        )
    if str(named) != str(principal.client_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This API token is limited to another client",
        )


def _touch(db: Session, token: ApiToken, integration) -> None:
    token.request_count = (token.request_count or 0) + 1
    now = _now()
    if token.last_used_at is None or (now - token.last_used_at).total_seconds() > TOUCH_AFTER_SECONDS:
        token.last_used_at = now
        if integration is not None:
            integration.last_used_at = now
    db.commit()


def _token_principal(db: Session, raw: str, request: Request) -> Principal:
    token = db.scalar(select(ApiToken).where(ApiToken.token_hash == digest(raw)))
    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token")
    if token.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This API token was revoked")
    if token.expires_at is not None and token.expires_at <= _now():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This API token expired")
    integration = token.integration
    if integration is None or integration.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This API integration was revoked")
    # Kommo's public limit is 7 requests per second, counted per credential.
    api_token_rate_limit.check(str(token.id))
    principal = Principal(
        agency_id=integration.agency_id,
        id=integration.created_by,
        name=f"API · {integration.name}",
        client_id=integration.client_id,
        scopes=frozenset(integration.scopes or ()),
        integration_id=integration.id,
        token_id=token.id,
        db=db,
    )
    _confine_to_client(principal, request)
    declared = _declared_scopes(request.scope.get("route"))
    if declared is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This route is not available to API tokens yet",
        )
    missing = sorted(set(declared) - set(principal.scopes))
    if missing:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This API token does not hold: {', '.join(missing)}",
        )
    _touch(db, token, integration)
    return principal


def get_current_user(
    request: Request,
    access_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """The person or the API integration behind the request."""
    raw = _bearer(authorization) or (x_api_key or "").strip()
    if raw.startswith("ol_"):
        return _token_principal(db, raw, request)
    if raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token")
    return _session_user(access_token, db)


def person_only(user=Depends(get_current_user)):
    """For the credential flows: signing in, changing a password, a portal
    session. An API token has no business there, and would fail obscurely."""
    if isinstance(user, Principal):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This operation needs a signed-in person, not an API token",
        )
    return user


def require(*needed: str):
    """A dependency that demands scopes of an API token.

    A person holds everything, so this only ever narrows a token. The refusal
    names the scopes that were missing, which is the difference between a
    five-minute fix and a support ticket.
    """

    required = tuple(needed)

    def dependency(user=Depends(get_current_user)):
        if isinstance(user, Principal):
            missing = sorted(set(required) - set(user.scopes))
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"This API token does not hold: {', '.join(missing)}",
                )
        return user

    # Read by _declared_scopes: this marker is what opens a route to API tokens.
    dependency.api_scopes = required
    return dependency
