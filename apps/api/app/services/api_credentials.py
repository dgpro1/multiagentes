"""Issuing API tokens and checking them.

A token is generated here, returned to its owner once and stored only as a
SHA-256 digest. It carries 256 bits of randomness, so a fast digest is enough
and a deliberately slow one (bcrypt) would tax every request for nothing; the
plaintext never reaches the database, so a leaked dump cannot be replayed.

The lifespan follows Kommo's long-lived tokens: the owner picks it, from one
day to five years.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import ApiIntegration, ApiToken

# Prefix of every token, so a leaked one is recognisable in a log.
TOKEN_PREFIX = "ol_"
LONG_LIVED = "long_lived"
AUTH_CODE = "auth_code"
ACCESS = "access"
REFRESH = "refresh"
MIN_DAYS = 1
MAX_DAYS = 365 * 5
# Authorization codes are single-use and short: just long enough for the
# redirect round-trip.
AUTH_CODE_MINUTES = 10
# Access tokens are short on purpose; callers renew them with the refresh
# token instead of holding a powerful secret for long.
ACCESS_MINUTES = 60
REFRESH_DAYS = 30
# Public OAuth client identifier, recognisable beside ol_ secrets.
OAUTH_CLIENT_PREFIX = "olic_"


def new_token() -> str:
    return f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def digest(raw: str) -> str:
    return hashlib.sha256(raw.strip().encode()).hexdigest()


def prefix_of(raw: str) -> str:
    """The part kept in clear so a token can be told apart in a list."""
    return raw[: len(TOKEN_PREFIX) + 6]


def expiry(days: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


def issue_long_lived(db: Session, integration: ApiIntegration, *, days: int) -> tuple[ApiToken, str]:
    """Create a token and return it beside its row. This is the only time the
    plaintext exists."""
    raw = new_token()
    token = ApiToken(
        integration_id=integration.id,
        kind=LONG_LIVED,
        token_hash=digest(raw),
        token_prefix=prefix_of(raw),
        expires_at=expiry(days),
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return token, raw


def active(token: ApiToken | None, now: datetime | None = None) -> bool:
    if token is None:
        return False
    if token.revoked_at is not None:
        return False
    if token.expires_at is not None and token.expires_at <= (now or datetime.now(timezone.utc)):
        return False
    return True


def new_oauth_client_id() -> str:
    return f"{OAUTH_CLIENT_PREFIX}{secrets.token_urlsafe(24)}"


def new_client_secret() -> str:
    return f"{TOKEN_PREFIX}cs_{secrets.token_urlsafe(32)}"


def _store(db: Session, integration: ApiIntegration, *, kind: str, minutes: int | None = None,
           days: int | None = None, scopes: list[str] | None = None,
           redirect_uri: str | None = None, grant_id=None) -> tuple[ApiToken, str]:
    """Create a token row and return it beside its one-time plaintext."""
    raw = new_token()
    if minutes is not None:
        expires = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    else:
        expires = expiry(days if days is not None else MAX_DAYS)
    token = ApiToken(
        integration_id=integration.id,
        kind=kind,
        token_hash=digest(raw),
        token_prefix=prefix_of(raw),
        expires_at=expires,
        scopes=scopes,
        redirect_uri=redirect_uri,
        grant_id=grant_id,
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return token, raw


def issue_auth_code(db: Session, integration: ApiIntegration, *, scopes: list[str], redirect_uri: str) -> tuple[ApiToken, str]:
    """A single-use code bound to the redirect it was approved for."""
    return _store(db, integration, kind=AUTH_CODE, minutes=AUTH_CODE_MINUTES,
                  scopes=scopes, redirect_uri=redirect_uri, grant_id=uuid.uuid4())


def issue_grant_pair(db: Session, integration: ApiIntegration, *, scopes: list[str], grant_id) -> tuple[tuple[ApiToken, str], tuple[ApiToken, str]]:
    """A short access token plus its refresh token, sharing one grant id so
    rotation and revocation retire the whole grant at once."""
    access = _store(db, integration, kind=ACCESS, minutes=ACCESS_MINUTES, scopes=scopes, grant_id=grant_id)
    refresh = _store(db, integration, kind=REFRESH, days=REFRESH_DAYS, scopes=scopes, grant_id=grant_id)
    return access, refresh
