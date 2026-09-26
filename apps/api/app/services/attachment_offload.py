"""Moves chat attachments out of Postgres into each client's own R2 bucket.

Attachments are always written to Postgres first, so an inbound webhook never
waits on Cloudflare. For clients that connected a bucket, this sweep later
copies the bytes there, records the key and clears ``data``; reads go through
``attachments.attachment_bytes``, which takes either form. Rows younger than
the grace period are left alone, so a reply that sends a file right after
storing it still finds the bytes in place. Clients without a bucket keep
their attachments in Postgres, as before.

It also migrates what older releases stored: the sweep does not care when a
row was written, only that its client has a bucket now.
"""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from ..models import Client, ClientStorageConnection, Conversation, Message, MessageAttachment, now_utc
from . import resource_storage

logger = logging.getLogger(__name__)

GRACE = timedelta(minutes=30)
BATCH = 200


def attachment_key(client: Client, attachment: MessageAttachment) -> str:
    return f"{client.agency_id}/{client.id}/attachments/{attachment.id}"


def offload_batch(db: Session, *, limit: int = BATCH, grace: timedelta = GRACE) -> int:
    """Move up to ``limit`` attachments; returns how many moved. A client whose
    bucket refuses a write is skipped for the rest of the batch."""
    rows = db.execute(
        select(MessageAttachment.id, Conversation.client_id)
        .join(Message, Message.id == MessageAttachment.message_id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .join(ClientStorageConnection, ClientStorageConnection.client_id == Conversation.client_id)
        .where(
            ClientStorageConnection.status == "connected",
            MessageAttachment.storage_key.is_(None),
            MessageAttachment.data.is_not(None),
            MessageAttachment.created_at < now_utc() - grace,
        )
        .order_by(MessageAttachment.created_at)
        .limit(limit)
    ).all()
    moved = 0
    failed_clients: set = set()
    for attachment_id, client_id in rows:
        if client_id in failed_clients:
            continue
        client = db.get(Client, client_id)
        attachment = db.scalar(
            select(MessageAttachment).options(undefer(MessageAttachment.data)).where(MessageAttachment.id == attachment_id)
        )
        if client is None or attachment is None or attachment.data is None:
            continue
        key = attachment_key(client, attachment)
        try:
            resource_storage.for_client(client).put(key, attachment.data, attachment.mime or "application/octet-stream")
        except Exception as exc:  # noqa: BLE001 - one client's bucket must not stop the others
            logger.warning("Could not move attachment %s to the bucket of client %s: %s", attachment_id, client_id, exc)
            failed_clients.add(client_id)
            db.rollback()
            continue
        attachment.storage_key = key
        attachment.data = None
        db.commit()
        moved += 1
    return moved
