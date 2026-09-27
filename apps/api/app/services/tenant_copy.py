"""Copies one client's data plane from the central database to its own.

Used when a client switches to its own database. It copies every data-plane
row that belongs to the client, table by table in dependency order and in
batches, all in one transaction on the target, and then compares row counts
per table; any mismatch rolls the whole copy back. Self-references
(a message quoting another, a thread merged into a lead) are filled in a
second pass so insertion order never matters.

The copy is a snapshot: the switch that uses it runs while the client's
channels are paused, so nothing is written centrally in between. The central
rows are kept until the switch is confirmed, which is what makes switching
back possible.
"""

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import Table, func, insert, select, update
from sqlalchemy.orm import Session

from ..data_plane import tenant_metadata
from ..database import Base
from ..models import Client

BATCH = 500


@dataclass
class CopyResult:
    counts: dict[str, int]


def _central(name: str) -> Table:
    return Base.metadata.tables[name]


def ownership(name: str, client_id, tables=None):
    """The WHERE clause that selects the client's rows of a data-plane table,
    against ``tables`` (a name -> Table lookup; the central tables by default,
    the client's own ones when counting what was copied)."""
    t = tables or _central
    table = t(name)
    if "client_id" in table.c:
        return table.c.client_id == client_id
    conversations = select(t("conversations").c.id).where(t("conversations").c.client_id == client_id)
    if name == "messages":
        return table.c.conversation_id.in_(conversations)
    if name == "message_attachments":
        messages = select(t("messages").c.id).where(t("messages").c.conversation_id.in_(conversations))
        return table.c.message_id.in_(messages)
    if name == "contact_tag_links":
        tags = select(t("contact_tags").c.id).where(t("contact_tags").c.client_id == client_id)
        return table.c.tag_id.in_(tags)
    if name == "professional_services":
        professionals = select(t("professionals").c.id).where(t("professionals").c.client_id == client_id)
        return table.c.professional_id.in_(professionals)
    raise ValueError(f"No ownership rule for data-plane table {name}")


def _self_references(table: Table) -> list[str]:
    return [c.name for c in table.columns for fk in c.foreign_keys if fk.column.table.name == table.name]


def central_connection(central: Session):
    """The session's connection to the central database, whichever client it is working on."""
    return central.connection(bind_arguments={"mapper": Client.__mapper__})


def _count(conn, tables, name: str, client_id) -> int:
    table = tables(name)
    return conn.scalar(select(func.count()).select_from(table).where(ownership(name, client_id, tables)))


def holds_client(conn, tables, client_id) -> bool:
    return any(
        conn.scalar(select(func.count()).select_from(tables(t.name)).where(tables(t.name).c.client_id == client_id))
        for t in tenant_metadata().sorted_tables if "client_id" in t.c
    )


def copy_rows(source, source_tables, target, target_tables, client_id) -> dict[str, int]:
    """Copy one client's data-plane rows from ``source`` to ``target`` (two
    connections, each with its own name -> Table lookup), in dependency order,
    then verify the counts. Raises on any mismatch; the caller's transaction
    on ``target`` then rolls everything back."""
    counts: dict[str, int] = {}
    for spec in tenant_metadata().sorted_tables:
        src, dst = source_tables(spec.name), target_tables(spec.name)
        deferred = _self_references(spec)
        names = [c.name for c in spec.columns]
        query = select(*[src.c[n] for n in names]).where(ownership(spec.name, client_id, source_tables))
        copied = 0
        pending_refs: list[dict] = []
        for batch in source.execute(query.execution_options(yield_per=BATCH)).mappings().partitions(BATCH):
            rows = [dict(row) for row in batch]
            for row in rows:
                refs = {n: row[n] for n in deferred if row[n] is not None}
                if refs:
                    pending_refs.append({"_id": row["id"], **refs})
                    for n in refs:
                        row[n] = None
            target.execute(insert(dst), rows)
            copied += len(rows)
        for ref in pending_refs:
            row_id = ref.pop("_id")
            target.execute(update(dst).where(dst.c.id == row_id).values(**ref))
        counts[spec.name] = copied
    for spec in tenant_metadata().sorted_tables:
        before = _count(source, source_tables, spec.name, client_id)
        after = _count(target, target_tables, spec.name, client_id)
        if before != after or counts[spec.name] != before:
            raise HTTPException(
                status_code=500, detail=f"Copy verification failed on {spec.name}: {before} in the source, {after} copied"
            )
    return counts


def delete_rows(conn, tables, client_id) -> None:
    """Remove one client's data-plane rows, children first."""
    for spec in reversed(tenant_metadata().sorted_tables):
        table = tables(spec.name)
        for column in _self_references(spec):
            conn.execute(update(table).where(ownership(spec.name, client_id, tables)).values({column: None}))
        conn.execute(table.delete().where(ownership(spec.name, client_id, tables)))


def copy_client(central: Session, client: Client, target_engine) -> CopyResult:
    """Copy the client's data plane into its own database and verify it.

    Refused when that database already holds rows of this client, so a copy
    is never merged into another."""
    tenant = tenant_metadata()
    with target_engine.begin() as target:
        if holds_client(target, tenant.tables.__getitem__, client.id):
            raise HTTPException(status_code=409, detail="The client's database already holds this client's data; copy refused")
        counts = copy_rows(central_connection(central), _central, target, tenant.tables.__getitem__, client.id)
    return CopyResult(counts=counts)


def copy_back(central: Session, client: Client, source_engine) -> CopyResult:
    """Replace the client's central rows with what its own database holds now
    (used to switch back). Runs inside the session's central transaction; the
    caller commits it."""
    tenant = tenant_metadata()
    target = central_connection(central)
    delete_rows(target, _central, client.id)
    with source_engine.connect() as source:
        counts = copy_rows(source, tenant.tables.__getitem__, target, _central, client.id)
    return CopyResult(counts=counts)


def clear_client(engine, client_id) -> None:
    """Remove a client's rows from one database (a half-done copy, or the central
    copy once the client no longer needs it)."""
    tenant = tenant_metadata()
    with engine.begin() as conn:
        delete_rows(conn, tenant.tables.__getitem__, client_id)
