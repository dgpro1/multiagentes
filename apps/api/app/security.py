import base64
import hashlib
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from cryptography.fernet import Fernet

from .config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_access_token(user_id: str, session_version: int = 1) -> str:
    settings = get_settings()
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode(
        {"sub": user_id, "type": "agency", "ver": session_version, "exp": expires},
        settings.secret_key, algorithm="HS256",
    )


def access_token_version(token: str) -> int:
    """The session version a token was issued under; one issued before versions existed is 1."""
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return 1
    return int(payload.get("ver") or 1)


def decode_access_token(token: str) -> str | None:
    """An agency session, and only that. Tokens issued before session types
    existed carry no ``type`` and are accepted as agency sessions, the only
    thing they ever were; a portal or platform token is refused here."""
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if payload.get("type") not in (None, "agency"):
        return None
    return payload.get("sub")


def create_portal_token(client_id: str, portal_slug: str, portal_user_id: str | None = None) -> str:
    """Session for a client's portal.

    ``sub`` stays the client, so tokens issued before portal users existed keep
    resolving. ``pu`` names the person when there is one, which is what lets a
    reply be attributed and a device be tied to someone rather than to the whole
    business.
    """
    settings = get_settings()
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    payload: dict = {"sub": client_id, "portal_slug": portal_slug, "type": "portal", "exp": expires}
    if portal_user_id:
        payload["pu"] = portal_user_id
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_portal_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
        if payload.get("type") != "portal":
            return None
        return payload
    except jwt.PyJWTError:
        return None


def create_platform_token(admin_id: str, session_version: int) -> str:
    """A platform session: its own type and audience, so it can never resolve
    as an agency or portal session and theirs can never resolve as this. The
    ``ver`` claim carries the account's session version, which the dependency
    checks against the row on every request."""
    settings = get_settings()
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode(
        {"sub": admin_id, "type": "platform", "aud": "platform", "ver": session_version, "exp": expires},
        settings.secret_key,
        algorithm="HS256",
    )


def decode_platform_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"], audience="platform")
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "platform":
        return None
    return payload


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


def mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "••••••••"
    return f"{value[:3]}••••••••{value[-4:]}"
