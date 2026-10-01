"""What the central database's ON DELETE did for a client's own database.

In a client's own database, rows keep ids of central ones (a channel, a
portal user, a team) without foreign keys, so deleting the central row
leaves nothing to clear them. For clients whose data lives there, deleting
one of those central rows clears the references in the client's database,
the way ON DELETE SET NULL does centrally. A web chat's CASCADE (its
conversations deleted with it) is deliberately softened to SET NULL there:
the customer's own history is never deleted from HunterAI.

References to the client or agency themselves are not mirrored on purpose:
deleting a client never touches its own database.

The map is read from the models' own foreign keys, so a new reference is
covered without touching this file.
"""

import logging
from collections import defaultdict

from sqlalchemy import event, text
from sqlalchemy.orm import object_session

logger = logging.getLogger(__name__)

_SKIP_TARGETS = {"clients", "agencies", "agents"}  # never mirrored (agents are soft-deleted)


def _reference_map() -> dict[str, list[tuple[str, str]]]:
    from ..data_plane import DATA_PLANE
    from ..database import Base

    refs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for table in Base.metadata.sorted_tables:
        if table.name not in DATA_PLANE:
            continue
        for fk in table.foreign_keys:
            target = fk.column.table.name
            if target in DATA_PLANE or target in _SKIP_TARGETS:
                continue
            if fk.ondelete in ("SET NULL", "CASCADE") and fk.parent.nullable:
                refs[target].append((table.name, fk.parent.name))
    return dict(refs)


def _clear_in_client_database(mapper, connection, target) -> None:
    from ..database import store_of, tenant_engine
    from ..models import Client
    from ..security import decrypt_secret

    client_id = getattr(target, "client_id", None)
    session = object_session(target)
    if client_id is None or session is None:
        return
    client = session.get(Client, client_id)
    store = store_of(client)
    if store is None or not store.encrypted_dsn:
        return
    refs = _REFS.get(mapper.local_table.name, [])
    try:
        with tenant_engine(decrypt_secret(store.encrypted_dsn), store.schema_name, store.pool_size).begin() as conn:
            for table, column in refs:
                conn.execute(text(f'UPDATE {table} SET "{column}" = NULL WHERE "{column}" = :id'), {"id": target.id})
    except Exception:  # noqa: BLE001 - a dangling id is harmless; never block the delete
        logger.warning("Could not clear references to %s %s in client %s's database",
                       mapper.local_table.name, target.id, client_id)


_REFS: dict[str, list[tuple[str, str]]] = {}


def install() -> None:
    """Listen for deletes of every central table a client's data points at."""
    from ..database import Base

    _REFS.update(_reference_map())
    for mapper in Base.registry.mappers:
        if mapper.local_table.name in _REFS and not event.contains(mapper.class_, "after_delete", _clear_in_client_database):
            event.listen(mapper.class_, "after_delete", _clear_in_client_database)
