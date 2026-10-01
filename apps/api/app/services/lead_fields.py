"""The custom fields of a client's lead card, and how their values are checked.

Shared by the client portal and the agency's client page, which manage the same
rows from two doors. Both routers resolve the client their own way and hand it
here; every query is confined to that client, and the client itself was already
resolved inside the caller's agency.

A field's ``key`` (a slug of its label) and ``type`` never change once it
exists, because conversations store their values under the key. Deleting a
field leaves those values where they are: they are simply no longer returned.

Fields and the options of a select are numbered from one sequence per client,
starting at ``FIRST_CODE``: the number is what a prompt cites and what a lead
stores for a choice, so renaming either one changes nothing that refers to it.
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
from ..schemas_lead_card import LeadFieldCreate, LeadFieldOption, LeadFieldUpdate

MAX_FIELDS = 30
MAX_OPTIONS = 30
MAX_OPTION_LENGTH = 60
MAX_TEXT_LENGTH = 500
FIRST_CODE = 1000
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


def option_ids(field: LeadField) -> list[int]:
    return [option["id"] for option in field.options or []]


def option_label(field: LeadField, option_id: int) -> str | None:
    return next((option["label"] for option in field.options or [] if option["id"] == option_id), None)


def next_code(fields: list[LeadField]) -> int:
    """One past the highest number the client's fields and options use."""
    used = [code for field in fields for code in (field.code, *option_ids(field))]
    return max(used, default=FIRST_CODE - 1) + 1


def build_options(requested: list[LeadFieldOption | str], current: list[dict], first_free: int) -> list[dict]:
    """The options a select will offer, each with its code.

    An option sent with an id keeps it (that is a rename); one sent as a bare
    label keeps the id of the current option with that label, and anything
    else is new and numbered from ``first_free``. Labels are trimmed, non-empty,
    unique and short.
    """
    if len(requested) > MAX_OPTIONS:
        raise HTTPException(status_code=422, detail=f"A select can have up to {MAX_OPTIONS} options")
    current_ids = {option["id"] for option in current}
    id_by_label = {option["label"]: option["id"] for option in current}
    options: list[dict] = []
    for item in requested:
        label = (item if isinstance(item, str) else item.label).strip()
        if not label:
            raise HTTPException(status_code=422, detail="Options cannot be empty")
        if len(label) > MAX_OPTION_LENGTH:
            raise HTTPException(status_code=422, detail=f"An option can have up to {MAX_OPTION_LENGTH} characters")
        option_id = id_by_label.get(label) if isinstance(item, str) else item.id
        if option_id is not None and option_id not in current_ids:
            raise HTTPException(status_code=422, detail=f"Unknown option: {option_id}")
        options.append({"id": option_id, "label": label})
    if len({option["label"] for option in options}) != len(options):
        raise HTTPException(status_code=422, detail="Options must be different from each other")
    kept = [option["id"] for option in options if option["id"] is not None]
    if len(set(kept)) != len(kept):
        raise HTTPException(status_code=422, detail="An option appears twice")
    for option in options:
        if option["id"] is None:
            option["id"] = first_free
            first_free += 1
    return options


def list_fields(db: Session, client: Client, *, lock: bool = False) -> list[LeadField]:
    query = (
        select(LeadField)
        .where(LeadField.client_id == client.id)
        .order_by(LeadField.position, LeadField.created_at, LeadField.id)
    )
    # Numbering reads every code the client uses, so two changes at once wait
    # for each other instead of handing out the same number.
    return list(db.scalars(query.with_for_update() if lock else query))


def get_field(db: Session, client: Client, field_id: uuid.UUID) -> LeadField:
    row = db.scalar(select(LeadField).where(LeadField.id == field_id, LeadField.client_id == client.id))
    if not row:
        raise HTTPException(status_code=404, detail="Field not found")
    return row


def create_field(db: Session, client: Client, payload: LeadFieldCreate) -> LeadField:
    existing = list_fields(db, client, lock=True)
    if len(existing) >= MAX_FIELDS:
        raise HTTPException(status_code=409, detail=f"A client can have up to {MAX_FIELDS} lead fields")
    if payload.type != "select" and payload.options:
        raise HTTPException(status_code=422, detail="Only a select field has options")
    code = next_code(existing)
    options = build_options(payload.options, [], code + 1) if payload.type == "select" else []
    position = payload.position if payload.position is not None else max((row.position for row in existing), default=-1) + 1
    row = LeadField(
        agency_id=client.agency_id,
        client_id=client.id,
        key=unique_key(payload.label, {row.key for row in existing}),
        code=code,
        label=payload.label,
        type=payload.type,
        options=options,
        position=position,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # Two people adding a field at the same moment, before either had one to lock.
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
    if payload.options is not None:
        if row.type != "select":
            raise HTTPException(status_code=422, detail="Only a select field has options")
        row.options = build_options(payload.options, list(row.options or []), next_code(list_fields(db, client, lock=True)))
    db.commit()
    db.refresh(row)
    return row


def delete_field(db: Session, client: Client, field_id: uuid.UUID) -> None:
    row = get_field(db, client, field_id)
    db.delete(row)
    db.commit()


def field_out(row: LeadField) -> dict:
    return {
        "id": row.id, "key": row.key, "code": row.code, "label": row.label, "type": row.type,
        "options": [dict(option) for option in row.options or []], "position": row.position,
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
        # Stored as the option's code; a label is accepted and turned into it.
        chosen = _choice(field, value)
        if chosen is None:
            raise HTTPException(status_code=422, detail=problem + "choose one of the options")
        return chosen
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


def _choice(field: LeadField, value) -> int | None:
    """The code of the option ``value`` names, by code or by label."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value in option_ids(field) else None
    if isinstance(value, str):
        return next((option["id"] for option in field.options or [] if option["label"] == value), None)
    return None


def shown_values(fields: list[LeadField], stored: dict | None) -> dict:
    """The stored values that still have a definition and still fit its type.

    Values of a deleted field stay in the row but are never returned. A new
    field that reuses a deleted one's key must not surface a value of another
    type, so a value of the wrong shape is left out too, and so is the choice of
    an option that was removed.
    """
    shapes = {
        "text": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "date": lambda v: isinstance(v, str),
        "checkbox": lambda v: isinstance(v, bool),
    }
    stored = stored or {}
    shown: dict = {}
    for field in fields:
        if field.key not in stored:
            continue
        value = stored[field.key]
        if field.type == "select":
            value = _choice(field, value)
            if value is not None:
                shown[field.key] = value
        elif shapes.get(field.type, lambda v: False)(value):
            shown[field.key] = value
    return shown
