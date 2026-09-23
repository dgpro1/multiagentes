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
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import ApiIntegration, ApiToken

# Prefix of every token, so a leaked one is recognisable in a log.
TOKEN_PREFIX = "ol_"
LONG_LIVED = "long_lived"
MIN_DAYS = 1
MAX_DAYS = 365 * 5


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
