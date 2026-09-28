"""How many lines of each channel type an agency may connect, and how it shares them.

Two levels, both ceilings that only restrict what the levels below chose:

- the platform gives the **agency** a *quota* per channel type, set in
  ``/superadmin/agencies/{slug}/plan``; and
- the agency assigns *allocations* of that quota to its **clients**.

A line can be created when the agency is under its quota AND the client is under
its allocation. An absent key means **unlimited**, so an installation that never
sets one behaves exactly as it did before this existed — no backfill, no change.

Every channel table lives in the control plane (``app/data_plane.py``), so all of
this is a plain count: nothing depends on where a client keeps its data.

The switch that decides whether a channel type exists at all is a different
layer (``app/agency_features.py`` for the agency, ``app/portal_features.py`` for
the client's portal). Quotas only cap how many.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Agency, Client, SocialChannel, WhatsAppChannel, WhatsAppCloudChannel, WidgetChannel

# The channel types that can be counted, keyed exactly like the module catalog
# in app/agency_features.py so nothing new has to be learned.
CATALOG: tuple[str, ...] = (
    "channels.whatsapp",
    "channels.whatsapp_cloud",
    "channels.instagram",
    "channels.messenger",
    "channels.webchat",
)

# A typo should not create a nonsense ceiling; the panel's stepper stops here too.
MAX_QUOTA = 999

LABELS: dict[str, str] = {
    "channels.whatsapp": "WhatsApp QR",
    "channels.whatsapp_cloud": "WhatsApp API",
    "channels.instagram": "Instagram",
    "channels.messenger": "Messenger",
    "channels.webchat": "web chat",
}


def _clean_int(value: object, key: str) -> int | None:
    """One stored or submitted number: None means unlimited, and anything that
    is not a whole number in range is refused. ``bool`` is an ``int`` in Python
    and would slip through as 0/1, so it is rejected explicitly."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(status_code=422, detail=f"Module '{key}' must be a whole number or null")
    if value < 0 or value > MAX_QUOTA:
        raise HTTPException(status_code=422, detail=f"Module '{key}' must be between 0 and {MAX_QUOTA}")
    return value


def _unknown_keys(patch: dict) -> list[str]:
    return sorted(str(key) for key in patch if key not in CATALOG)


def normalize(stored: dict | None) -> dict[str, int | None]:
    """Every countable type, with its stored number or ``None`` for unlimited.
    Unknown keys are dropped and unusable values read as unlimited, so a
    hand-edited row can never leak into a response or a decision."""
    result: dict[str, int | None] = {key: None for key in CATALOG}
    if isinstance(stored, dict):
        for key in CATALOG:
            if key in stored:
                try:
                    result[key] = _clean_int(stored[key], key)
                except HTTPException:
                    result[key] = None
    return result


def validate_patch(patch: dict) -> dict[str, int | None]:
    """A submitted partial update: known keys, whole numbers or null."""
    if not isinstance(patch, dict):
        raise HTTPException(status_code=422, detail="channel_quotas must be an object of numbers")
    unknown = _unknown_keys(patch)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown channel type: {', '.join(unknown)}")
    return {key: _clean_int(value, key) for key, value in patch.items()}


def merge(stored: dict | None, patch: dict) -> dict[str, int | None]:
    """The stored numbers with ``patch`` applied. A ``null`` in the patch means
    unlimited, so the key is removed instead of stored as null."""
    merged = {key: value for key, value in normalize(stored).items() if value is not None}
    for key, value in validate_patch(patch).items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def quota_of(agency: Agency, key: str) -> int | None:
    return normalize(getattr(agency, "channel_quotas", None)).get(key)


def allocation_of(client: Client, key: str) -> int | None:
    return normalize(getattr(client, "channel_allocations", None)).get(key)


# --- Counting ----------------------------------------------------------------

def _rows(key: str):
    """The table (and provider) one channel type lives in."""
    if key == "channels.instagram":
        return SocialChannel, "instagram"
    if key == "channels.messenger":
        return SocialChannel, "messenger"
    model = {
        "channels.whatsapp": WhatsAppChannel,
        "channels.whatsapp_cloud": WhatsAppCloudChannel,
        "channels.webchat": WidgetChannel,
    }[key]
    return model, None


def _scoped(query, model, provider: str | None, *, agency_id=None, client_id=None):
    if provider is not None:
        query = query.where(model.provider == provider)
    if agency_id is not None:
        query = query.where(model.agency_id == agency_id)
    if client_id is not None:
        query = query.where(model.client_id == client_id)
    return query


def _count(db: Session, key: str, *, agency_id: uuid.UUID | None = None, client_id: uuid.UUID | None = None) -> int:
    """Rows of one channel type, per agency or per client. Every existing row
    counts — connected or not, enabled or not — because a disconnected line
    still holds a number and a live Evolution instance; deleting it frees the
    slot."""
    model, provider = _rows(key)
    query = _scoped(select(func.count()).select_from(model), model, provider,
                    agency_id=agency_id, client_id=client_id)
    return int(db.scalar(query) or 0)


def _counts_by_client(db: Session, key: str, agency_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """One query for every client of the agency, instead of one per client."""
    model, provider = _rows(key)
    query = _scoped(
        select(model.client_id, func.count()).select_from(model).where(model.agency_id == agency_id).group_by(model.client_id),
        model, provider,
    )
    return {row[0]: int(row[1]) for row in db.execute(query)}


def used_by_agency(db: Session, agency_id: uuid.UUID, key: str) -> int:
    return _count(db, key, agency_id=agency_id)


def used_by_client(db: Session, client_id: uuid.UUID, key: str) -> int:
    return _count(db, key, client_id=client_id)


def used_map(db: Session, agency_id: uuid.UUID) -> dict[str, int]:
    """Every channel type's count for one agency, for a screen that shows them all."""
    return {key: used_by_agency(db, agency_id, key) for key in CATALOG}


# --- The ceiling -------------------------------------------------------------

def check(db: Session, agency: Agency, client: Client, key: str) -> None:
    """Refuse a line that would exceed the client's allocation or the agency's
    quota. Called at every point that creates a channel row, inside the same
    transaction as the insert.

    The agency row is locked first: two simultaneous creations would otherwise
    both read "under the quota" and both insert, breaking the promise the
    platform made to the agency.
    """
    db.execute(select(Agency.id).where(Agency.id == agency.id).with_for_update())

    label = LABELS.get(key, key)
    allocation = allocation_of(client, key)
    used_client = used_by_client(db, client.id, key)
    if allocation is not None and used_client >= allocation:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This client already uses the {allocation} {label} line(s) assigned to it. "
                "Remove one, or assign it more from the agency's pool."
            ),
        )

    quota = quota_of(agency, key)
    used_agency = used_by_agency(db, agency.id, key)
    if quota is not None and used_agency >= quota:
        raise HTTPException(
            status_code=409,
            detail=(
                f"The agency already uses all {quota} {label} line(s) of its plan. "
                "Ask the platform to raise the plan."
            ),
        )


# --- Reporting ---------------------------------------------------------------

def usage(db: Session, agency: Agency) -> list[dict]:
    """What the agency has, what it uses, and how it spread it — the platform's
    planning view and the agency's own summary. One query per channel type."""
    quotas = normalize(getattr(agency, "channel_quotas", None))
    clients = list(db.scalars(select(Client).where(Client.agency_id == agency.id).order_by(Client.name)))
    out: list[dict] = []
    for key in CATALOG:
        counts = _counts_by_client(db, key, agency.id)
        by_client = []
        for client in clients:
            allocation = allocation_of(client, key)
            used = counts.get(client.id, 0)
            if used or allocation is not None:
                by_client.append({
                    "client_id": client.id,
                    "client_name": client.name,
                    "used": used,
                    "allocation": allocation,
                })
        out.append({
            "key": key,
            "label": LABELS.get(key, key),
            "used": sum(counts.values()),
            "quota": quotas.get(key),
            "by_client": by_client,
        })
    return out


def allowance(db: Session, agency: Agency, client: Client) -> list[dict]:
    """One client's view: the agency's quota, its own allocation, what it uses
    and how much room is left."""
    quotas = normalize(getattr(agency, "channel_quotas", None))
    allocations = normalize(getattr(client, "channel_allocations", None))
    out: list[dict] = []
    for key in CATALOG:
        quota = quotas.get(key)
        allocation = allocations.get(key)
        used = used_by_client(db, client.id, key)
        ceilings = [value for value in (quota, allocation) if value is not None]
        allowed = min(ceilings) if ceilings else None
        out.append({
            "key": key,
            "label": LABELS.get(key, key),
            "agency_quota": quota,
            "allocation": allocation,
            "used": used,
            "allowed": allowed,
            "remaining": None if allowed is None else max(allowed - used, 0),
        })
    return out
