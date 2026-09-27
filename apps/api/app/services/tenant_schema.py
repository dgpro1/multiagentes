"""The schema of a client's own database, and how it moves forward.

Revisions are plain SQL files in ``migrations_tenant/`` named ``tNNNN_name.sql``,
applied in order and recorded in ``hunterai_schema_version`` inside the
client's schema. They are frozen once shipped: a change to a data-plane model
needs a new file, which ``tests/test_data_plane.py`` enforces by comparing the
schema the files build with the models.

Each file runs in one transaction, so a failed revision leaves the client on
the previous one. Every connected client database is brought forward at boot
(``upgrade_all``, from main.py) and on demand from the panel. A client using
its own database whose schema is behind is not read or written until it is
(``database.use_client`` raises DataMoving; webhooks are kept and replayed).
"""

import logging
import re
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import tenant_engine
from ..models import Client, now_utc
from ..security import decrypt_secret

REVISIONS_DIR = Path(__file__).resolve().parents[2] / "migrations_tenant"
VERSION_TABLE = "hunterai_schema_version"
_NAME = re.compile(r"^(t\d{4})_[a-z0-9_]+\.sql$")


@lru_cache
def revisions() -> list[tuple[str, Path]]:
    found = sorted((m.group(1), path) for path in REVISIONS_DIR.glob("*.sql") if (m := _NAME.match(path.name)))
    return found


def head() -> str:
    return revisions()[-1][0]


def upgrade_engine(engine) -> list[str]:
    """Apply every pending revision on ``engine``; returns the ones applied."""
    applied_now: list[str] = []
    with engine.begin() as conn:
        conn.exec_driver_sql(
            f"CREATE TABLE IF NOT EXISTS {VERSION_TABLE} (revision VARCHAR(40) PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {row[0] for row in conn.execute(text(f"SELECT revision FROM {VERSION_TABLE}"))}
    for revision, path in revisions():
        if revision in done:
            continue
        with engine.begin() as conn:
            conn.exec_driver_sql(path.read_text(encoding="utf-8"))
            conn.execute(text(f"INSERT INTO {VERSION_TABLE} (revision) VALUES (:r)"), {"r": revision})
        applied_now.append(revision)
    return applied_now


logger = logging.getLogger(__name__)


def upgrade_all(db: Session) -> list:
    """Bring every connected client database to the latest revision, one at a
    time; returns the ids of clients using their own database that were
    brought up to date (their kept webhooks can be replayed). A failure is
    recorded on that client's connection and the rest go on."""
    from sqlalchemy import select

    from ..models import ClientDataStore

    updated = []
    stores = list(db.scalars(select(ClientDataStore).where(ClientDataStore.status == "connected")))
    for store in stores:
        if store.schema_version == head():
            continue
        client = db.get(Client, store.client_id)
        try:
            upgrade(db, client)
            if client.data_mode == "supabase":
                updated.append(client.id)
        except Exception:  # noqa: BLE001 - recorded on the store by upgrade()
            logger.warning("Tenant schema update failed for client %s", store.client_id)
            db.rollback()
    return updated


def upgrade(db: Session, client: Client) -> str:
    """Bring the client's own database to the latest tenant revision."""
    from .data_store import SCHEMA

    store = client.data_store
    if not store or store.status != "connected" or not store.encrypted_dsn:
        raise HTTPException(status_code=409, detail="Connect the client's Supabase project first")
    try:
        upgrade_engine(tenant_engine(decrypt_secret(store.encrypted_dsn), SCHEMA))
    except Exception as exc:  # noqa: BLE001 - reported to the panel, never the DSN
        store.last_error = f"The schema update failed: {type(exc).__name__}"
        db.commit()
        raise HTTPException(status_code=502, detail=store.last_error) from exc
    store.schema_version = head()
    store.last_error = None
    store.last_checked_at = now_utc()
    db.commit()
    return store.schema_version
