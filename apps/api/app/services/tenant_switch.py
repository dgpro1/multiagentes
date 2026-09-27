"""Moving a client's data between the central database and its own.

``to_own_database`` copies the client's data plane into its connected
Supabase project and switches it there; ``back_to_central`` does the reverse
with whatever the client's database holds by then. While either runs the
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

from ..database import new_session, tenant_engine, use_client
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


def _client_engine(client: Client):
    from .data_store import SCHEMA

    store = client.data_store
    if not store or store.status != "connected" or not store.encrypted_dsn:
        raise HTTPException(status_code=409, detail="Connect the client's Supabase project first")
    return tenant_engine(decrypt_secret(store.encrypted_dsn), SCHEMA)


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

    from ..models import ClientDataStore

    with new_session() as db:
        waiting = list(db.scalars(
            select(PendingInbound.client_id).where(PendingInbound.processed_at.is_(None)).distinct()
        ))
    replayed = 0
    for client_id in waiting:
        with new_session() as db:
            client = db.get(Client, client_id)
            if client is None or client.data_mode != "supabase":
                continue  # a switch in progress replays its own when it ends
            try:
                with _client_engine(client).connect() as conn:
                    conn.execute(text("SELECT 1"))
            except Exception:  # noqa: BLE001 - still down; next sweep
                continue
            store = db.scalar(select(ClientDataStore).where(ClientDataStore.client_id == client_id))
            if store is not None:
                store.last_error = None
                store.last_checked_at = now_utc()
                db.commit()
        replayed += await replay_pending(client_id)
    return replayed


async def to_own_database(db: Session, client: Client) -> dict:
    if client.data_mode != "central":
        raise HTTPException(status_code=409, detail="The client is not using the central database")
    store = client.data_store
    engine = _client_engine(client)
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
        client.data_mode = "supabase"
        db.commit()
    except Exception:
        db.rollback()
        clear_client(engine, client.id)
        _mark(db, client, "central")
        await replay_pending(client.id)
        raise
    await replay_pending(client.id)
    return {"data_mode": "supabase", "counts": result.counts}


async def back_to_central(db: Session, client: Client) -> dict:
    if client.data_mode != "supabase":
        raise HTTPException(status_code=409, detail="The client is not using its own database")
    engine = _client_engine(client)
    _mark(db, client, "switching")
    try:
        await asyncio.sleep(SETTLE_SECONDS)
        result = copy_back(db, client, engine)
        db.commit()
    except Exception:
        db.rollback()
        _mark(db, client, "supabase")
        await replay_pending(client.id)
        raise
    _mark(db, client, "central")
    await replay_pending(client.id)
    return {"data_mode": "central", "counts": result.counts}
