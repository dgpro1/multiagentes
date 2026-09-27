"""Chat message attachments: persistence and serving helpers.

The original media bytes are stored next to the message (Postgres LargeBinary,
same pattern as knowledge documents). The LLM pipeline never reads them — it
uses the text resolved into ``Message.llm_content`` at ingestion time.
"""

import re
import uuid
from urllib.parse import quote

from fastapi import HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Conversation, Message, MessageAttachment

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

# How many files one widget conversation may hold. The widget is public and
# unauthenticated, so without a ceiling a visitor can write into the primary
# database indefinitely.
MAX_WIDGET_ATTACHMENTS = 30

# Types that must never be accepted. Serving already forces a download for
# anything not renderable, so this is a second line rather than the only one.
REJECTED_UPLOAD_MIMES = {
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
    "image/svg",
    "application/x-msdownload",
    "application/x-msdos-program",
    "application/vnd.microsoft.portable-executable",
}


def ensure_uploadable(mime: str) -> None:
    """Reject types a browser would happily execute."""
    if (mime or "").lower().split(";")[0].strip() in REJECTED_UPLOAD_MIMES:
        raise HTTPException(status_code=400, detail="That file type is not allowed")


def attachment_kind(mime: str) -> str:
    mime = (mime or "").lower()
    if mime.startswith("image/"):
        return "image"
    # video/ogg is what browsers label opus voice notes, not a real video.
    if mime.startswith("audio/") or mime.startswith("video/ogg"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    return "file"


def safe_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    return re.sub(r"[^\w. -]", "_", filename)[:255] or None


# What a visitor's file may weigh, by kind, before its bytes are kept. WhatsApp
# itself caps images at 5 MB and video/audio at 16 MB; documents keep the
# general ceiling.
INBOUND_LIMITS = {"image": 5 * 1024 * 1024, "video": 16 * 1024 * 1024, "audio": 16 * 1024 * 1024}


def inbound_limit(kind: str) -> int:
    return INBOUND_LIMITS.get(kind, MAX_ATTACHMENT_BYTES)


def store_visitor_attachment(
    db: Session,
    message: Message,
    *,
    data: bytes,
    mime: str,
    filename: str | None = None,
    kind: str | None = None,
) -> MessageAttachment | None:
    """``store_attachment`` for a file a visitor already sent (it cannot be
    refused): over its kind's limit, only a notice is kept on the message, in
    the customer's language like the other conversation markers."""
    kind = kind or attachment_kind(mime)
    if len(data) <= inbound_limit(kind):
        return store_attachment(db, message, data=data, mime=mime, filename=filename, kind=kind)
    size = f"{len(data) / (1024 * 1024):.1f} MB"
    notice = f"[Archivo demasiado grande para guardarse: {safe_filename(filename) or kind}, {size}]"
    message.content = f"{message.content}\n{notice}".strip() if message.content else notice
    if message.llm_content:
        message.llm_content = f"{message.llm_content}\n{notice}"
    return None


def store_attachment(
    db: Session,
    message: Message,
    *,
    data: bytes,
    mime: str,
    filename: str | None = None,
    kind: str | None = None,
) -> MessageAttachment:
    attachment = MessageAttachment(
        message_id=message.id,
        kind=kind or attachment_kind(mime),
        mime=(mime or "application/octet-stream")[:100],
        filename=safe_filename(filename),
        size_bytes=len(data),
        data=data,
    )
    db.add(attachment)
    return attachment


def conversation_attachment(db: Session, conversation: Conversation, attachment_id: uuid.UUID) -> MessageAttachment:
    """Load an attachment ensuring it belongs to the given conversation, or to
    any thread merged into the same lead."""
    from .lead_group import group_ids

    attachment = db.scalar(
        select(MessageAttachment)
        .join(Message, Message.id == MessageAttachment.message_id)
        .where(MessageAttachment.id == attachment_id, Message.conversation_id.in_(group_ids(db, conversation)))
    )
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return attachment


# Types the chat UI renders in place. Everything else is sent as a download with
# a neutral content type: the uploader chooses the mime, and attachments are
# served from the application's own origin, so a document that the browser is
# willing to execute must never be rendered there.
INLINE_PREFIXES = ("image/", "audio/", "video/")
# SVG is an image the browser will happily run scripts from.
NEVER_INLINE = {"image/svg+xml", "image/svg"}


def is_inline_safe(mime: str) -> bool:
    mime = (mime or "").lower()
    return mime.startswith(INLINE_PREFIXES) and mime not in NEVER_INLINE


def logo_response(data: bytes, mime: str) -> Response:
    """Serve an agency/client logo. Logos are shown via ``<img>`` (where SVG
    never runs scripts), but they are served from the app's own origin, so a
    logo opened directly by URL must not execute: nosniff stops type
    second-guessing and the CSP neutralizes any script an SVG logo carries
    while still letting it render as an image."""
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Cache-Control": "private, max-age=300",
            "Vary": "Origin",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        },
    )


def content_disposition(filename: str | None, *, inline: bool = False) -> str:
    """ASCII fallback plus RFC 5987 preserves Unicode names in HTTP headers."""
    filename = safe_filename(filename) or "attachment"
    ascii_filename = filename.encode("ascii", "ignore").decode() or "attachment"
    disposition = "inline" if inline else "attachment"
    return f'{disposition}; filename="{ascii_filename}"; filename*=UTF-8\'\'{quote(filename, safe="")}'


def _bucket_of(attachment: MessageAttachment):
    """The client's bucket an offloaded attachment was moved to."""
    from sqlalchemy.orm import object_session

    from ..models import Client
    from . import resource_storage

    client = object_session(attachment).get(Client, attachment.message.conversation.client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return resource_storage.for_client(client)


def attachment_bytes(attachment: MessageAttachment) -> bytes:
    """The attachment's bytes, from Postgres or, once moved, from the client's own bucket.

    A moved file needs the bucket connected: disconnecting it (or the customer
    deleting the object) answers 409/502 instead of an empty file."""
    if attachment.data is not None:
        return attachment.data
    if not attachment.storage_key:
        raise HTTPException(status_code=404, detail="The attachment has no data")
    from . import resource_storage

    try:
        return _bucket_of(attachment).get(attachment.storage_key)
    except resource_storage.StorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def read_attachment(attachment: MessageAttachment) -> bytes:
    """``attachment_bytes`` for async callers: the bucket read runs off the event loop."""
    if attachment.data is not None or not attachment.storage_key:
        return attachment_bytes(attachment)
    import asyncio

    from . import resource_storage

    store = _bucket_of(attachment)
    try:
        return await asyncio.to_thread(store.get, attachment.storage_key)
    except resource_storage.StorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def attachment_response(attachment: MessageAttachment) -> Response:
    return file_response(attachment_bytes(attachment), attachment.mime, attachment.filename)


def file_response(data: bytes, mime: str, filename: str | None) -> Response:
    inline = is_inline_safe(mime)
    headers = {
        "Cache-Control": "private, max-age=3600",
        # Stop the browser from second-guessing the type we send.
        "X-Content-Type-Options": "nosniff",
        # The response is cacheable for an hour and CORS headers are only added
        # when the request carries an Origin, so without this a copy stored from
        # a direct hit is replayed to cross-origin readers and rejected. Only
        # matters when the API is served from its own domain.
        "Vary": "Origin",
    }
    headers["Content-Disposition"] = content_disposition(filename, inline=inline)
    media_type = mime if inline else "application/octet-stream"
    return Response(content=data, media_type=media_type, headers=headers)


def llm_text(message: Message) -> str:
    """The text the LLM should see for a stored message."""
    return message.llm_content or message.content
