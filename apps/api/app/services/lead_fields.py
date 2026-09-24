"""The custom fields of a client's lead card, and how their values are checked.

Shared by the client portal and the agency's client page, which manage the same
rows from two doors. Both routers resolve the client their own way and hand it
here; every query is confined to that client, and the client itself was already
resolved inside the caller's agency.

A field's ``key`` (a slug of its label) and ``type`` never change once it
exists, because conversations store their values under the key. Deleting a
field leaves those values where they are: they are simply no longer returned.
"""

import math
import re
import unicodedata
import uuid
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Client, LeadField
from ..schemas_lead_card import LeadFieldCreate, LeadFieldUpdate

MAX_FIELDS = 30
MAX_OPTIONS = 30
MAX_OPTION_LENGTH = 60
MAX_TEXT_LENGTH = 500
_KEY_LENGTH = 50  # leaves room for the _2, _3 suffix inside String(60)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def slugify(label: str) -> str:
    """ASCII lowercase words joined by underscores; ``field`` when nothing is left."""
    ascii_label = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "_", ascii_label).strip("_")[:_KEY_LENGTH].strip("_") or "field"


def unique_key(label: str, taken: set[str]) -> str:
    base = slugify(label)
    key, n = base, 2
    while key in taken:
        key = f"{base}_{n}"
        n += 1
    return key


def clean_options(options: list[str]) -> list[str]:
    """Trimmed, non-empty, unique and short; the list a select offers."""
    cleaned = [option.strip() for option in options]
    if len(cleaned) > MAX_OPTIONS:
        raise HTTPException(status_code=422, detail=f"A select can have up to {MAX_OPTIONS} options")
    if any(not option for option in cleaned):
        raise HTTPException(status_code=422, detail="Options cannot be empty")
    if any(len(option) > MAX_OPTION_LENGTH for option in cleaned):
        raise HTTPException(status_code=422, detail=f"An option can have up to {MAX_OPTION_LENGTH} characters")
    if len(set(cleaned)) != len(cleaned):
        raise HTTPException(status_code=422, detail="Options must be different from each other")
    return cleaned


def list_fields(db: Session, client: Client) -> list[LeadField]:
    return list(
        db.scalars(
            select(LeadField)
            .where(LeadField.client_id == client.id)
            .order_by(LeadField.position, LeadField.created_at, LeadField.id)
        )
    )


def get_field(db: Session, client: Client, field_id: uuid.UUID) -> LeadField:
    row = db.scalar(select(LeadField).where(LeadField.id == field_id, LeadField.client_id == client.id))
    if not row:
        raise HTTPException(status_code=404, detail="Field not found")
    return row


def create_field(db: Session, client: Client, payload: LeadFieldCreate) -> LeadField:
    existing = list_fields(db, client)
    if len(existing) >= MAX_FIELDS:
        raise HTTPException(status_code=409, detail=f"A client can have up to {MAX_FIELDS} lead fields")
    if payload.type != "select" and payload.options:
        raise HTTPException(status_code=422, detail="Only a select field has options")
    options = clean_options(payload.options) if payload.type == "select" else []
    position = payload.position if payload.position is not None else max((row.position for row in existing), default=-1) + 1
    row = LeadField(
        agency_id=client.agency_id,
        client_id=client.id,
        key=unique_key(payload.label, {row.key for row in existing}),
        label=payload.label,
        type=payload.type,
        options=options,
        position=position,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # Two people adding a field with the same label at the same moment.
        db.rollback()
        raise HTTPException(status_code=409, detail="A field with this name already exists, try again") from None
    db.refresh(row)
    return row


def update_field(db: Session, client: Client, field_id: uuid.UUID, payload: LeadFieldUpdate) -> LeadField:
    row = get_field(db, client, field_id)
    values = payload.model_dump(exclude_unset=True)
    if values.get("label") is not None:
        row.label = values["label"]
    if values.get("position") is not None:
        row.position = values["position"]
    if values.get("options") is not None:
        if row.type != "select":
            raise HTTPException(status_code=422, detail="Only a select field has options")
        row.options = clean_options(values["options"])
    db.commit()
    db.refresh(row)
    return row


def delete_field(db: Session, client: Client, field_id: uuid.UUID) -> None:
    row = get_field(db, client, field_id)
    db.delete(row)
    db.commit()


def field_out(row: LeadField) -> dict:
    return {
        "id": row.id, "key": row.key, "label": row.label, "type": row.type,
        "options": list(row.options or []), "position": row.position,
    }


def check_value(field: LeadField, value):
    """``value`` as the field stores it, or a 422 that names the field."""
    problem = f"{field.label}: "
    if field.type == "text":
        if not isinstance(value, str) or len(value) > MAX_TEXT_LENGTH:
            raise HTTPException(status_code=422, detail=problem + f"enter text of up to {MAX_TEXT_LENGTH} characters")
        return value
    if field.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise HTTPException(status_code=422, detail=problem + "enter a number")
        return value
    if field.type == "date":
        if not isinstance(value, str) or not _DATE.match(value):
            raise HTTPException(status_code=422, detail=problem + "use the date format YYYY-MM-DD")
        try:
            date.fromisoformat(value)
        except ValueError:
            raise HTTPException(status_code=422, detail=problem + "that date does not exist") from None
        return value
    if field.type == "select":
        if not isinstance(value, str) or value not in (field.options or []):
            raise HTTPException(status_code=422, detail=problem + "choose one of the options")
        return value
    if field.type == "checkbox":
        if not isinstance(value, bool):
            raise HTTPException(status_code=422, detail=problem + "use true or false")
        return value
    raise HTTPException(status_code=422, detail=problem + "unknown field type")


def merge_values(fields: list[LeadField], stored: dict | None, changes: dict) -> dict:
    """The stored values with ``changes`` applied: null clears a key, an
    unknown key or a bad value is a 422 and nothing is written."""
    by_key = {field.key: field for field in fields}
    unknown = sorted(key for key in changes if key not in by_key)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown field: {', '.join(unknown)}")
    merged = dict(stored or {})
    for key, value in changes.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = check_value(by_key[key], value)
    return merged


def shown_values(fields: list[LeadField], stored: dict | None) -> dict:
    """The stored values that still have a definition and still fit its type.

    Values of a deleted field stay in the row but are never returned. A new
    field that reuses a deleted one's key must not surface a value of another
    type, so a value of the wrong shape is left out too.
    """
    shapes = {
        "text": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "date": lambda v: isinstance(v, str),
        "select": lambda v: isinstance(v, str),
        "checkbox": lambda v: isinstance(v, bool),
    }
    stored = stored or {}
    return {
        field.key: stored[field.key]
        for field in fields
        if field.key in stored and shapes.get(field.type, lambda v: False)(stored[field.key])
    }
