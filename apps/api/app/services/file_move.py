"""Moving a client's files between its own R2 bucket and the agency's.

The keys of a client's objects already start with the agency and client ids
(``resource_storage.new_key``, ``attachment_offload.attachment_key``), so the
agency's bucket holds every client in a folder of its own and a file keeps the
same key wherever it lives. That makes the move a plain copy: every object the
database points at is read from the bucket it is in, written to the other one
and checked by size, and only then does ``hosted_by`` change. A failure leaves
the client exactly where it was.

Files that arrive while the copy runs are caught by a second pass over the
database. The originals are left in the bucket they came from: a client's own
bucket is theirs, and the agency's keeps them until it cleans them up.
"""

import asyncio
import logging

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Agency,
    AgencyStorageConnection,
    Client,
    ClientResource,
    Conversation,
    Message,
    MessageAttachment,
)
from ..security import decrypt_secret
from . import agency_backend, resource_storage as storage, storage_connection

logger = logging.getLogger(__name__)

# A move runs inside one request, so it is for libraries that fit in one. The
# client's own quota caps what it can hold (1 GB unless the agency raised it).
MAX_MOVE_BYTES = 2 * 1024 * 1024 * 1024
PASSES = 3


def objects_of(db: Session, client: Client) -> dict[str, str]:
    """Every object key the client's data points at, with its content type."""
    found: dict[str, str] = {}
    for key, mime in db.execute(
        select(ClientResource.storage_key, ClientResource.mime)
        .where(ClientResource.client_id == client.id, ClientResource.storage_key.is_not(None))
    ):
        found[key] = mime or "application/octet-stream"
    for key, mime in db.execute(
        select(MessageAttachment.storage_key, MessageAttachment.mime)
        .join(Message, Message.id == MessageAttachment.message_id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.client_id == client.id, MessageAttachment.storage_key.is_not(None))
    ):
        found.setdefault(key, mime or "application/octet-stream")
    return found


def stored_bytes(db: Session, client: Client) -> int:
    resources = db.scalar(
        select(func.coalesce(func.sum(ClientResource.size_bytes), 0))
        .where(ClientResource.client_id == client.id, ClientResource.storage_key.is_not(None))
    ) or 0
    attachments = db.scalar(
        select(func.coalesce(func.sum(MessageAttachment.size_bytes), 0))
        .join(Message, Message.id == MessageAttachment.message_id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.client_id == client.id, MessageAttachment.storage_key.is_not(None))
    ) or 0
    return int(resources) + int(attachments)


def _copy(source: storage.Storage, target: storage.Storage, objects: dict[str, str], done: set[str]) -> None:
    for key, mime in objects.items():
        if key in done:
            continue
        try:
            data = source.get(key)
        except storage.StorageError as exc:
            # A row whose file is already gone from the bucket has nothing to move.
            logger.warning("Skipping %s while moving files: %s", key, exc)
            done.add(key)
            continue
        target.put(key, data, mime)
        if target.size(key) != len(data):
            raise storage.StorageError("The destination bucket kept a different size than was sent")
        done.add(key)


def own_bucket(conn) -> storage.Storage:
    """The client's own bucket, proven again right now: it is about to be the only copy."""
    if not conn or not conn.encrypted_access_key_id or not conn.encrypted_secret:
        raise HTTPException(status_code=409, detail="Connect the client's own bucket first")
    try:
        bucket = storage.Storage(
            conn.account_ref, decrypt_secret(conn.encrypted_access_key_id), decrypt_secret(conn.encrypted_secret),
            conn.bucket, conn.region,
        )
        bucket.probe()
    except storage.StorageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return bucket


def agency_bucket(db: Session, client: Client, *, entering: bool = False) -> storage.Storage:
    """The agency's bucket. Entering it needs the platform's module; reading
    from it to take a client out never does."""
    if entering:
        agency_backend.ensure_module(db.get(Agency, client.agency_id))
    conn = db.scalar(select(AgencyStorageConnection).where(AgencyStorageConnection.agency_id == client.agency_id))
    if not conn or conn.status != "connected":
        raise HTTPException(status_code=409, detail="Connect the agency's Cloudflare bucket first")
    return storage.storage_of(conn)


async def move(db: Session, client: Client, target: str) -> dict:
    """Take the client's files to ``target``: ``agency`` (the agency's bucket) or
    ``client`` (its own). Returns how many objects were copied."""
    if target not in ("agency", "client"):
        raise HTTPException(status_code=422, detail="Unknown file location")
    if client.data_mode == "switching":
        raise HTTPException(status_code=409, detail="This client's data is being moved to another database. Try again in a minute.")
    conn = client.storage_connection
    hosted = conn.hosted_by if conn else "client"
    if hosted == target:
        raise HTTPException(status_code=409, detail="The client's files are already there")

    if target == "agency":
        destination = agency_bucket(db, client, entering=True)
        source = storage.for_connection(conn) if conn and conn.status == "connected" and conn.encrypted_access_key_id else None
    else:
        destination = own_bucket(conn)
        source = agency_bucket(db, client)

    if source is not None:
        size = stored_bytes(db, client)
        if size > MAX_MOVE_BYTES:
            raise HTTPException(status_code=409, detail="This library is too large to move in one go; ask the platform to move it")
        done: set[str] = set()
        try:
            for _ in range(PASSES):
                objects = objects_of(db, client)
                if not set(objects) - done:
                    break
                await asyncio.to_thread(_copy, source, destination, objects, done)
        except storage.StorageError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    else:
        done = set()

    conn = storage_connection.get_or_create(db, client)
    conn.hosted_by = target
    # Hosted by the agency the row carries no credentials of its own to prove, and
    # moving back proved them just now: either way it is usable.
    conn.status, conn.last_error = "connected", None
    db.commit()
    db.refresh(conn)
    return {"hosted_by": conn.hosted_by, "copied": len(done)}
