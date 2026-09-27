"""Everything a client owns, as a ZIP of JSON Lines files, offered before the
client is deleted.

One file per table, one row per line, so a client with millions of messages is
written in batches to a temporary file instead of being built in memory. Only
an allowlist of tables is exported, and within them every column that holds a
secret or raw bytes is left out: credentials never leave, and files are listed
by name (the resource library's files stay in the customer's own bucket).
"""

import json
import tempfile
import zipfile
from datetime import date, datetime
from decimal import Decimal
import uuid

from sqlalchemy import LargeBinary, select
from sqlalchemy.orm import Session

from ..models import (
    Agent,
    Appointment,
    CannedResponse,
    Client,
    ClientResource,
    Contact,
    ContactIdentity,
    ContactTag,
    ContactTagLink,
    Conversation,
    LeadField,
    Message,
    MessageAttachment,
    PipelineStage,
    Professional,
    ScheduledMessage,
    Service,
    Team,
    now_utc,
)

BATCH = 1000

# Never exported, whatever table they sit in.
_SECRET_MARKERS = ("encrypted", "password", "secret", "token", "api_key")
_ALWAYS_SKIP = {"logo_data", "file_data", "data", "embedding", "portal_domain_token"}


def _skip(column) -> bool:
    name = column.name
    if name in _ALWAYS_SKIP or isinstance(column.type, LargeBinary):
        return True
    # Token *counts* on usage-style columns are data, not credentials.
    if name.endswith("_tokens"):
        return False
    return any(marker in name for marker in _SECRET_MARKERS)


def _value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return None
    return value


def _row(obj) -> dict:
    return {c.name: _value(getattr(obj, c.key, None)) for c in obj.__table__.columns if not _skip(c)}


def _write(archive: zipfile.ZipFile, name: str, rows) -> int:
    count = 0
    with archive.open(f"{name}.jsonl", "w") as handle:
        for obj in rows:
            handle.write((json.dumps(_row(obj), ensure_ascii=False, default=str) + "\n").encode("utf-8"))
            count += 1
    return count


def build_export(db: Session, client: Client):
    """A spooled temporary file holding the ZIP, rewound, and its suggested filename."""
    spool = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
    counts: dict[str, int] = {}

    def by_client(model):
        return db.scalars(select(model).where(model.client_id == client.id).execution_options(yield_per=BATCH))

    conversation_ids = select(Conversation.id).where(Conversation.client_id == client.id)
    with zipfile.ZipFile(spool, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        counts["client"] = _write(archive, "client", [client])
        for name, model in (
            ("agents", Agent),
            ("contacts", Contact),
            ("contact_identities", ContactIdentity),
            ("contact_tags", ContactTag),
            ("conversations", Conversation),
            ("pipeline_stages", PipelineStage),
            ("lead_fields", LeadField),
            ("appointments", Appointment),
            ("professionals", Professional),
            ("services", Service),
            ("scheduled_messages", ScheduledMessage),
            ("teams", Team),
            ("canned_responses", CannedResponse),
            ("resources", ClientResource),
        ):
            counts[name] = _write(archive, name, by_client(model))
        counts["contact_tag_links"] = _write(archive, "contact_tag_links", db.scalars(
            select(ContactTagLink).join(ContactTag, ContactTag.id == ContactTagLink.tag_id)
            .where(ContactTag.client_id == client.id).execution_options(yield_per=BATCH)
        ))
        counts["messages"] = _write(archive, "messages", db.scalars(
            select(Message).where(Message.conversation_id.in_(conversation_ids))
            .order_by(Message.conversation_id, Message.created_at).execution_options(yield_per=BATCH)
        ))
        counts["message_attachments"] = _write(archive, "message_attachments", db.scalars(
            select(MessageAttachment).join(Message, Message.id == MessageAttachment.message_id)
            .where(Message.conversation_id.in_(conversation_ids)).execution_options(yield_per=BATCH)
        ))
        archive.writestr("README.json", json.dumps({
            "client": client.name,
            "exported_at": now_utc().isoformat(),
            "format": "One JSON object per line in each .jsonl file.",
            "not_included": [
                "Credentials and secrets of channels, calendars, storage and portal users.",
                "The bytes of message attachments and knowledge documents (listed by name only).",
                "Files of the resource library: they stay in the client's own Cloudflare R2 bucket.",
            ],
            "counts": counts,
        }, ensure_ascii=False, indent=2))
    spool.seek(0)
    stamp = now_utc().strftime("%Y%m%d")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in client.name.lower()).strip("-") or "client"
    return spool, f"hunterai-{safe}-{stamp}.zip"
