"""Exactly-once writes for the versioned API.

A caller sends ``Idempotency-Key`` with a POST. The first request claims a
row and stores its answer; a retry with the same body replays the stored
answer with ``"api_replay": true`` instead of acting twice, while the same
key with another body is refused and a key still in flight answers 409.
Rows live 24 hours; anything older is treated as a new request. Keys are
scoped per credential owner inside the agency, so integrations and people
never replay each other.
"""

import hashlib
import json
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import ApiIdempotencyKey, now_utc

TTL_HOURS = 24
MAX_KEY_LENGTH = 64


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _cutoff() -> datetime:
    return now_utc() - timedelta(hours=TTL_HOURS)


def owner_of(user) -> str:
    """The credential scope a key belongs to: one integration, or one person."""
    integration_id = getattr(user, "integration_id", None)
    if integration_id is not None:
        return f"integration:{integration_id}"
    return f"user:{getattr(user, 'id', 'unknown')}"


def canonical_body(body: dict) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


class Receipt:
    """The outcome of presenting a key: replay it, proceed, or refuse."""

    def __init__(self, row: ApiIdempotencyKey | None, conflict: str | None = None):
        self.row = row
        self.conflict = conflict

    @property
    def replay(self) -> dict | None:
        if self.row is not None and self.row.response_status is not None:
            body = dict(self.row.response_body or {})
            body["api_replay"] = True
            return {"status": self.row.response_status, "body": body}
        return None


def use_key(db: Session, *, agency_id, owner: str, key: str, endpoint: str, body: dict) -> Receipt:
    """Claim the key for this request, or find what it already means."""
    key = (key or "").strip()
    if not key:
        return Receipt(None)
    if len(key) > MAX_KEY_LENGTH:
        raise HTTPException(status_code=422, detail="Idempotency keys take 64 characters at most")
    db.execute(
        delete(ApiIdempotencyKey).where(
            ApiIdempotencyKey.agency_id == agency_id, ApiIdempotencyKey.created_at < _cutoff()
        )
    )
    wanted = _hash(canonical_body(body))
    row = db.scalar(
        select(ApiIdempotencyKey).where(
            ApiIdempotencyKey.agency_id == agency_id,
            ApiIdempotencyKey.owner_key == owner,
            ApiIdempotencyKey.key_hash == _hash(key),
        )
    )
    if row is not None:
        if row.response_status is None:
            raise HTTPException(status_code=409, detail="This request is already in flight")
        if row.request_hash != wanted:
            raise HTTPException(
                status_code=422, detail="This idempotency key was already used with another request"
            )
        return Receipt(row)
    claim = ApiIdempotencyKey(
        agency_id=agency_id, owner_key=owner, key_hash=_hash(key),
        endpoint=endpoint, request_hash=wanted,
    )
    db.add(claim)
    try:
        db.flush()
    except IntegrityError:
        # Lost the race with a twin request: read what it claimed.
        db.rollback()
        twin = db.scalar(
            select(ApiIdempotencyKey).where(
                ApiIdempotencyKey.agency_id == agency_id,
                ApiIdempotencyKey.owner_key == owner,
                ApiIdempotencyKey.key_hash == _hash(key),
            )
        )
        if twin is None or twin.response_status is None:
            raise HTTPException(status_code=409, detail="This request is already in flight")
        if twin.request_hash != wanted:
            raise HTTPException(
                status_code=422, detail="This idempotency key was already used with another request"
            )
        return Receipt(twin)
    return Receipt(claim)


def complete(db: Session, receipt: Receipt, *, status: int, body: dict) -> None:
    """Store the answer a claimed request produced."""
    if receipt.row is None:
        return
    receipt.row.response_status = status
    receipt.row.response_body = body
    db.commit()


def abandon(db: Session, receipt: Receipt) -> None:
    """Release a claim whose request failed, so retrying is allowed."""
    if receipt.row is None:
        return
    db.delete(receipt.row)
    db.commit()
