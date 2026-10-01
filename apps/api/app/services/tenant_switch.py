"""Moving a client's data between the central database and another place.

That place is the client's own Supabase project (``to_own_database``) or a
schema of its agency's project (``to_agency``). ``move`` goes from any place to
any other, through the central database when neither end is central:
``back_to_central`` copies everything back from wherever the client's data
lives by then, and the second hop copies it out again. While any of them runs the
client is "switching": requests answer 503 (``database.DataMoving``), sweeps
skip it, and webhooks are kept in ``hunterai_pending_inbound`` and replayed
here once the move ends, whichever way it ends.

Every copy is verified row by row per table and runs in one transaction on
the receiving side, so a failure leaves nothing half-copied: the client simply
stays where it was. Once the copy in the client's database is verified, the
central rows are removed in the same transaction that switches the mode: a
stale central copy would show up twice in agency-wide views and, worse, be
picked up by the central sweeps (a second reply to an old conversation).
Moving back copies everything from the client's database, so nothing is lost.
"""

import asyncio
import logging
from typing import Awaitable, Callable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import OWN_DATABASE_MODES, new_session, store_of, tenant_engine, use_client
from ..models import Client, PendingInbound, now_utc
from ..security import decrypt_secret
from . import tenant_schema
from .tenant_copy import central_connection, clear_client, copy_back, copy_client, delete_rows, _central

logger = logging.getLogger(__name__)

# Requests that resolved the client just before it started switching may still
# be writing; give them a moment before the copy reads.
SETTLE_SECONDS = 2.0

Replayer = Callable[[Session, dict], Awaitable[None]]
_replayers: dict[str, Replayer] = {}


def register_replayer(source: str, replay: Replayer) -> None:
    """Webhook routers register how to re-run an event they had to keep."""
    _replayers[source] = replay


def _engine(store, missing: str):
    if not store or store.status != "connected" or not store.encrypted_dsn:
        raise HTTPException(status_code=409, detail=missing)
    return tenant_engine(decrypt_secret(store.encrypted_dsn), store.schema_name, store.pool_size)


def _client_engine(client: Client):
    """The engine of the client's own Supabase project (connected, possibly not in use yet)."""
    return _engine(client.data_store, "Connect the client's Supabase project first")


def _current_engine(client: Client):
    """The engine of wherever the client's data lives right now."""
    return _engine(store_of(client), "This client's database is not connected")


def _mark(db: Session, client: Client, mode: str) -> None:
    """Set where the client's data lives. The session is pointed at the central
    database first: the copies read and write each side explicitly."""
    use_client(db, None)
    client.data_mode = mode
    db.commit()


async def replay_pending(client_id) -> int:
    """Re-run the webhooks kept while the client was switching, oldest first."""
    replayed = 0
    with new_session() as db:
        ids = list(db.scalars(
            select(PendingInbound.id)
            .where(PendingInbound.client_id == client_id, PendingInbound.processed_at.is_(None))
            .order_by(PendingInbound.created_at)
        ))
    for pending_id in ids:
        with new_session() as db:
            pending = db.get(PendingInbound, pending_id)
            if pending is None or pending.processed_at is not None:
                continue
            replay = _replayers.get(pending.source)
            payload = pending.payload
            pending.processed_at = now_utc()
            db.commit()
            if replay is None:
                logger.error("No replayer for kept webhook source %s", pending.source)
                continue
            try:
                await replay(db, payload)
                replayed += 1
            except Exception:  # noqa: BLE001 - one bad event must not hold back the rest
                logger.exception("Replaying a kept webhook failed")
    return replayed


async def retry_kept() -> int:
    """Replay what was kept for clients whose own database had stopped
    answering, once it answers again. Run periodically from main.py."""
    from sqlalchemy import text

    with new_session() as db:
        waiting = list(db.scalars(
            select(PendingInbound.client_id).where(PendingInbound.processed_at.is_(None)).distinct()
        ))
    replayed = 0
    for client_id in waiting:
        with new_session() as db:
            client = db.get(Client, client_id)
            if client is None or client.data_mode not in OWN_DATABASE_MODES:
                continue  # a switch in progress replays its own when it ends
            try:
                with _current_engine(client).connect() as conn:
                    conn.execute(text("SELECT 1"))
            except Exception:  # noqa: BLE001 - still down; next sweep
                continue
            store = store_of(client)
            if store is not None:
                store.last_error = None
                store.last_checked_at = now_utc()
                db.commit()
        replayed += await replay_pending(client_id)
    return replayed


async def _move_in(db: Session, client: Client, mode: str, store, engine) -> dict:
    """Copy the client's central data into ``engine`` and switch it to ``mode``.
    Whichever way it ends the client's kept webhooks are replayed."""
    if client.data_mode != "central":
        raise HTTPException(status_code=409, detail="The client is not using the central database")
    if (store.schema_version or "") != tenant_schema.head():
        raise HTTPException(status_code=409, detail="Prepare the client's database first (its tables are not up to date)")
    _mark(db, client, "switching")
    try:
        await asyncio.sleep(SETTLE_SECONDS)
        # Rows left there by an earlier move back are older than the central
        # ones, which are the truth now: replace them.
        clear_client(engine, client.id)
        result = copy_client(db, client, engine)
        db.commit()
    except Exception:
        db.rollback()
        _mark(db, client, "central")
        await replay_pending(client.id)
        raise
    try:
        # The verified copy is now the only one: the central rows go in the
        # same transaction that switches the mode.
        use_client(db, None)
        delete_rows(central_connection(db), _central, client.id)
        client.data_mode = mode
        db.commit()
    except Exception:
        db.rollback()
        clear_client(engine, client.id)
        _mark(db, client, "central")
        await replay_pending(client.id)
        raise
    await replay_pending(client.id)
    return {"data_mode": mode, "counts": result.counts}


async def to_own_database(db: Session, client: Client) -> dict:
    if client.data_mode != "central":
        raise HTTPException(status_code=409, detail="The client is not using the central database")
    engine = _client_engine(client)
    return await _move_in(db, client, "supabase", client.data_store, engine)


async def to_agency(db: Session, client: Client) -> dict:
    """Move a central client into a schema of its agency's Supabase project,
    creating the schema and its role first if the client has none yet."""
    from . import agency_backend

    if client.data_mode != "central":
        raise HTTPException(status_code=409, detail="The client is not using the central database")
    schema = await agency_backend.provision_client(db, client)
    result = await _move_in(db, client, "agency", schema, _engine(schema, "This client's agency schema is not ready"))
    schema.retired_at = None
    db.commit()
    return result


async def back_to_central(db: Session, client: Client) -> dict:
    if client.data_mode not in OWN_DATABASE_MODES:
        raise HTTPException(status_code=409, detail="The client's data is already in the central database")
    leaving = client.data_mode
    engine = _current_engine(client)
    _mark(db, client, "switching")
    try:
        await asyncio.sleep(SETTLE_SECONDS)
        result = copy_back(db, client, engine)
        db.commit()
    except Exception:
        db.rollback()
        _mark(db, client, leaving)
        await replay_pending(client.id)
        raise
    _mark(db, client, "central")
    if leaving == "agency" and client.agency_schema is not None:
        # The rows stay in the agency's schema as a safety copy until it is dropped.
        client.agency_schema.retired_at = now_utc()
        db.commit()
    await replay_pending(client.id)
    return {"data_mode": "central", "counts": result.counts}


async def move(db: Session, client: Client, target: str) -> dict:
    """Take the client's data to ``target`` ("central", "supabase" or "agency")
    from wherever it is. Between two places that are not the central database
    it goes through it; if the second hop fails the client simply stays central,
    with all its data."""
    if target not in ("central", "supabase", "agency"):
        raise HTTPException(status_code=422, detail="Unknown data location")
    if client.data_mode == "switching":
        raise HTTPException(status_code=409, detail="This client's data is already being moved")
    if client.data_mode == target:
        raise HTTPException(status_code=409, detail="The client's data is already there")
    result: dict = {"data_mode": client.data_mode, "counts": {}}
    if client.data_mode in OWN_DATABASE_MODES:
        result = await back_to_central(db, client)
        if target == "central":
            return result
    if target == "supabase":
        return await to_own_database(db, client)
    if target == "agency":
        return await to_agency(db, client)
    return result
