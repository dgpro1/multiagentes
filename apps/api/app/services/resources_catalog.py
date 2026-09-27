"""The resource library: files and links a client's agent may send.

Shared by the client portal and the agency's client page, which manage the same
rows from two doors. Both routers resolve the client their own way and hand it
here; every query is confined to that client, and the client itself was already
resolved inside the caller's agency. Files live in the client's own R2 bucket
(``services/resource_storage.py``); only their metadata is stored here.
"""

import logging
import re
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Client, ClientResource, Contact
from ..schemas_resources import ResourceCreate, ResourceUpdate
from . import resource_storage as storage
from . import storage_connection
from .attachments import attachment_kind, ensure_uploadable, safe_filename
from .image_resize import shrink_image

logger = logging.getLogger(__name__)

MAX_RESOURCES = 200


def list_resources(db: Session, client: Client, active_only: bool = False) -> list[ClientResource]:
    query = select(ClientResource).where(ClientResource.client_id == client.id)
    if active_only:
        query = query.where(ClientResource.is_active.is_(True))
    return list(db.scalars(query.order_by(ClientResource.position, ClientResource.created_at, ClientResource.id)))


def get_resource(db: Session, client: Client, resource_id: uuid.UUID) -> ClientResource:
    row = db.scalar(
        select(ClientResource).where(ClientResource.id == resource_id, ClientResource.client_id == client.id)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Resource not found")
    return row


def _ensure_free_name(db: Session, client: Client, name: str, *, ignore: uuid.UUID | None = None) -> None:
    query = select(ClientResource.id).where(
        ClientResource.client_id == client.id, func.lower(ClientResource.name) == name.lower()
    )
    if ignore:
        query = query.where(ClientResource.id != ignore)
    if db.scalar(query):
        raise HTTPException(status_code=409, detail="Another resource already has that name")


def _next_position(db: Session, client: Client, requested: int) -> int:
    if requested > 0:
        return requested
    return int(db.scalar(select(func.count()).select_from(ClientResource).where(ClientResource.client_id == client.id)) or 0)


def _ensure_room(db: Session, client: Client) -> None:
    count = db.scalar(select(func.count()).select_from(ClientResource).where(ClientResource.client_id == client.id))
    if (count or 0) >= MAX_RESOURCES:
        raise HTTPException(status_code=409, detail=f"A client can have up to {MAX_RESOURCES} resources")


def create_link(db: Session, client: Client, payload: ResourceCreate) -> ClientResource:
    if not payload.url:
        raise HTTPException(status_code=422, detail="A link resource needs a url")
    _ensure_room(db, client)
    _ensure_free_name(db, client, payload.name)
    row = ClientResource(
        agency_id=client.agency_id,
        client_id=client.id,
        kind="link",
        name=payload.name,
        description=payload.description.strip(),
        is_active=payload.is_active,
        position=_next_position(db, client, payload.position),
        url=payload.url,
        message_template=payload.message_template.strip(),
    )
    return _save(db, row)


def create_file(
    db: Session, client: Client, payload: ResourceCreate, *, data: bytes, mime: str, filename: str | None
) -> ClientResource:
    conn = client.storage_connection
    if not conn or conn.status != "connected":
        raise HTTPException(status_code=409, detail="storage_not_connected")
    mime = (mime or "application/octet-stream").split(";")[0].strip().lower()
    ensure_uploadable(mime)
    if not data:
        raise HTTPException(status_code=422, detail="The file is empty")
    media_kind = attachment_kind(mime)
    if media_kind == "image":
        # Accept the photo the business has (up to the ceiling) and fit it
        # to what WhatsApp shows; the per-kind limit applies to the result.
        if len(data) > storage.CEILING_FILE_MB * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"An image can be at most {storage.CEILING_FILE_MB} MB")
        data, mime = shrink_image(data, mime)
    limit = storage.max_bytes(conn, media_kind)
    if len(data) > limit:
        raise HTTPException(
            status_code=413, detail=f"A {media_kind} can be at most {limit // (1024 * 1024)} MB for this client"
        )
    if storage_connection.used_bytes(db, client) + len(data) > storage.quota_bytes(conn):
        raise HTTPException(status_code=413, detail="This client's library is out of space")
    _ensure_room(db, client)
    _ensure_free_name(db, client, payload.name)

    key = storage.new_key(client)
    store = storage.for_connection(conn)
    try:
        store.put(key, data, mime)
    except storage.StorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    row = ClientResource(
        agency_id=client.agency_id,
        client_id=client.id,
        kind="file",
        name=payload.name,
        description=payload.description.strip(),
        is_active=payload.is_active,
        position=_next_position(db, client, payload.position),
        storage_key=key,
        media_kind=media_kind,
        mime=mime[:120],
        filename=safe_filename(filename),
        size_bytes=len(data),
    )
    try:
        return _save(db, row)
    except HTTPException:
        _forget_object(client, key)
        raise


def _save(db: Session, row: ClientResource) -> ClientResource:
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:  # two requests raced for the same name
        db.rollback()
        raise HTTPException(status_code=409, detail="Another resource already has that name") from exc
    db.refresh(row)
    return row


def update_resource(db: Session, client: Client, resource_id: uuid.UUID, payload: ResourceUpdate) -> ClientResource:
    row = get_resource(db, client, resource_id)
    values = payload.model_dump(exclude_unset=True)
    if values.get("name") is not None:
        _ensure_free_name(db, client, values["name"], ignore=row.id)
        row.name = values["name"]
    if values.get("description") is not None:
        row.description = values["description"].strip()
    if values.get("is_active") is not None:
        row.is_active = values["is_active"]
    if values.get("position") is not None:
        row.position = values["position"]
    if row.kind == "link":
        if "url" in values:
            if not values["url"]:
                raise HTTPException(status_code=422, detail="A link resource needs a url")
            row.url = values["url"]
        if values.get("message_template") is not None:
            row.message_template = values["message_template"].strip()
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Another resource already has that name") from exc
    db.refresh(row)
    return row


def delete_resource(db: Session, client: Client, resource_id: uuid.UUID) -> None:
    row = get_resource(db, client, resource_id)
    key = row.storage_key if row.kind == "file" else None
    db.delete(row)
    db.commit()
    if key:
        _forget_object(client, key)


def _forget_object(client: Client, key: str) -> None:
    """Best effort: an object that cannot be removed now is only an orphan in the customer's own bucket."""
    try:
        storage.for_client(client).delete(key)
    except (HTTPException, storage.StorageError):
        logger.warning("Could not delete %s from the bucket of client %s", key, client.id)


def read_file(client: Client, row: ClientResource) -> bytes:
    if row.kind != "file" or not row.storage_key:
        raise HTTPException(status_code=404, detail="That resource has no file")
    try:
        return storage.for_client(client).get(row.storage_key)
    except storage.StorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


_VARIABLE = re.compile(r"\{\{\s*([a-z_.]+)\s*\}\}")


def render_link_text(row: ClientResource, client: Client, contact: Contact | None = None) -> str:
    """The message a link resource sends: its template with the variables filled
    in, and the address itself when the template does not carry it."""
    values = {
        "url": row.url or "",
        "client.name": client.name or "",
        "contact.name": ((contact.name or contact.whatsapp_contact_name or "") if contact else "").strip(),
    }
    template = (row.message_template or "").strip()
    text = _VARIABLE.sub(lambda m: values.get(m.group(1), ""), template) if template else ""
    if row.url and row.url not in text:
        text = f"{text}\n{row.url}".strip()
    return text
