"""The client's own R2 bucket: connecting, checking and disconnecting it.

Shared by the agency's client page and the client portal (both resolve the
client their own way and hand it here) and by the public onboarding link, which
the business owner opens without an OpenLivery account to paste the credentials
they created in their own Cloudflare.

Credentials are probed (write, read, delete) before they are kept, encrypted
like every other secret, and never returned; only a hint of the key id is.
"""

import logging
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Agency, Client, ClientResource, ClientStorageConnection, new_public_id, now_utc
from ..schemas_resources import StorageConnect, StorageLimitsUpdate
from ..security import decrypt_secret, encrypt_secret
from . import resource_storage as storage

logger = logging.getLogger(__name__)

# What a client without a connection row is shown; the column defaults match.
DEFAULT_MAX_FILE_MB = 10
DEFAULT_QUOTA_MB = 1024


def _frontend() -> str:
    return get_settings().frontend_url.rstrip("/")


def _link_expiry():
    return now_utc() + timedelta(days=get_settings().storage_link_days)


def connect_url(conn: ClientStorageConnection) -> str:
    return f"{_frontend()}/connect/storage/{conn.connect_token}"


def get_connection(client: Client) -> ClientStorageConnection | None:
    return client.storage_connection


def get_or_create(db: Session, client: Client) -> ClientStorageConnection:
    conn = client.storage_connection
    if conn:
        return conn
    conn = ClientStorageConnection(
        agency_id=client.agency_id, client_id=client.id, connect_expires_at=_link_expiry()
    )
    # Through the relationship, so the client the caller holds sees it too.
    client.storage_connection = conn
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return conn


def used_bytes(db: Session, client: Client) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(ClientResource.size_bytes), 0)).where(
                ClientResource.client_id == client.id, ClientResource.kind == "file"
            )
        )
        or 0
    )


def _key_hint(conn: ClientStorageConnection) -> str:
    if not conn.encrypted_access_key_id:
        return ""
    try:
        return "••••" + decrypt_secret(conn.encrypted_access_key_id)[-4:]
    except Exception:  # noqa: BLE001 - a key that no longer decrypts is reported by status, not here
        return ""


def out(db: Session, client: Client) -> dict:
    from . import agency_backend

    agency_ready = agency_backend.storage_ready(db, db.get(Agency, client.agency_id))
    conn = client.storage_connection
    if not conn:
        return {
            "status": "none",
            "agency_storage_ready": agency_ready,
            "max_file_mb": DEFAULT_MAX_FILE_MB,
            "quota_mb": DEFAULT_QUOTA_MB,
            "used_bytes": 0,
            "link_active": False,
        }
    if conn.hosted_by == "agency":
        # The agency's bucket holds the files: nothing of it is shown here, and
        # the client's own credentials, if it kept any, only say whether moving
        # back is possible.
        return {
            "status": conn.status,
            "hosted_by": "agency",
            "agency_storage_ready": agency_ready,
            "provider": "agency",
            "access_key_hint": _key_hint(conn),
            "last_error": conn.last_error,
            "last_checked_at": conn.last_checked_at,
            "connected_at": conn.connected_at,
            "max_file_mb": conn.max_file_mb,
            "quota_mb": conn.quota_mb,
            "used_bytes": used_bytes(db, client),
            "link_active": conn.connect_expires_at > now_utc(),
        }
    return {
        "status": conn.status,
        "hosted_by": "client",
        "agency_storage_ready": agency_ready,
        "provider": conn.provider,
        "account_id": conn.account_ref,
        "bucket": conn.bucket,
        "access_key_hint": _key_hint(conn),
        "last_error": conn.last_error,
        "last_checked_at": conn.last_checked_at,
        "connected_at": conn.connected_at,
        "max_file_mb": conn.max_file_mb,
        "quota_mb": conn.quota_mb,
        "used_bytes": used_bytes(db, client),
        "link_active": conn.connect_expires_at > now_utc(),
    }


def connect(db: Session, client: Client, payload: StorageConnect) -> ClientStorageConnection:
    """Probe the credentials and keep them only if the bucket takes a write, a read and a delete."""
    if not storage.valid_account_id(payload.account_id):
        raise HTTPException(status_code=422, detail="The Cloudflare account id must be 32 hexadecimal characters")
    if not storage.valid_bucket(payload.bucket):
        raise HTTPException(status_code=422, detail="That is not a valid bucket name")
    conn = get_or_create(db, client)
    try:
        store = storage.Storage(payload.account_id, payload.access_key_id, payload.secret_access_key, payload.bucket)
        store.probe()
    except storage.StorageError as exc:
        conn.last_error = str(exc)
        conn.last_checked_at = now_utc()
        if conn.status != "connected":
            conn.status = "error"
        db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    conn.account_ref = payload.account_id
    conn.bucket = payload.bucket
    conn.encrypted_access_key_id = encrypt_secret(payload.access_key_id)
    conn.encrypted_secret = encrypt_secret(payload.secret_access_key)
    conn.status = "connected"
    conn.last_error = None
    conn.last_checked_at = conn.connected_at = now_utc()
    db.commit()
    db.refresh(conn)
    return conn


def recheck(db: Session, client: Client) -> ClientStorageConnection:
    """Probe the stored credentials again, e.g. after the customer rotated a token."""
    conn = client.storage_connection
    if not conn or not conn.encrypted_access_key_id:
        raise HTTPException(status_code=409, detail="storage_not_connected")
    try:
        storage.Storage(
            conn.account_ref,
            decrypt_secret(conn.encrypted_access_key_id),
            decrypt_secret(conn.encrypted_secret or ""),
            conn.bucket,
            conn.region,
        ).probe()
        conn.status = "connected"
        conn.last_error = None
    except storage.StorageError as exc:
        conn.status = "error"
        conn.last_error = str(exc)
    conn.last_checked_at = now_utc()
    db.commit()
    db.refresh(conn)
    return conn


def update_limits(db: Session, client: Client, payload: StorageLimitsUpdate) -> ClientStorageConnection:
    conn = get_or_create(db, client)
    if payload.max_file_mb is not None:
        conn.max_file_mb = min(payload.max_file_mb, storage.CEILING_FILE_MB)
    if payload.quota_mb is not None:
        conn.quota_mb = min(payload.quota_mb, storage.CEILING_QUOTA_MB)
    db.commit()
    db.refresh(conn)
    return conn


def disconnect(db: Session, client: Client) -> ClientStorageConnection | None:
    """Forget the credentials. The files stay in the customer's bucket: they are theirs."""
    conn = client.storage_connection
    if not conn:
        return None
    conn.encrypted_access_key_id = None
    conn.encrypted_secret = None
    conn.account_ref = ""
    conn.bucket = ""
    if conn.hosted_by != "agency":
        # An agency-hosted client keeps working on the agency's bucket; only its own credentials go.
        conn.status = "pending"
        conn.last_error = None
        conn.connected_at = None
    db.commit()
    db.refresh(conn)
    return conn


def renew_link(db: Session, client: Client) -> ClientStorageConnection:
    """A fresh link; the previous one stops working."""
    conn = get_or_create(db, client)
    conn.connect_token = new_public_id()
    conn.connect_expires_at = _link_expiry()
    db.commit()
    db.refresh(conn)
    return conn


# The public onboarding link.

def by_link(db: Session, token: str) -> ClientStorageConnection:
    conn = db.scalar(select(ClientStorageConnection).where(ClientStorageConnection.connect_token == token))
    if not conn or conn.connect_expires_at <= now_utc():
        raise HTTPException(status_code=404, detail="This link is not valid or has expired")
    return conn


def link_info(db: Session, token: str) -> dict:
    conn = by_link(db, token)
    agency = db.get(Agency, conn.agency_id)
    return {
        "client_name": conn.client.name,
        "agency_name": agency.name if agency else "",
        "status": conn.status,
        "bucket": conn.bucket,
        "expires_at": conn.connect_expires_at,
    }


def connect_by_link(db: Session, token: str, payload: StorageConnect) -> ClientStorageConnection:
    conn = by_link(db, token)
    return connect(db, conn.client, payload)
