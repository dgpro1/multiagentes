import hashlib
import threading

from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


# A NAT or pooler between the app and a remote Postgres can drop idle TCP
# connections silently (no RST); a dead pooled socket then hangs even
# pool_pre_ping's SELECT 1 until the kernel's retransmission timeout.
# Keepalives surface the break within ~60s and pool_recycle retires
# connections before they can go stale in the first place.
KEEPALIVE_CONNECT_ARGS = {
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
    "connect_timeout": 10,
}

engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    pool_recycle=240,
    connect_args=KEEPALIVE_CONNECT_ARGS,
)


class RoutingSession(Session):
    """A session that sends a client's own tables to that client's database.

    ``use_client(session, client)`` marks the session as working on one
    client. While the client keeps its data in its own database, the
    data-plane tables (app/data_plane.py) are read and written there and
    everything else stays central; for every other client, and for a session
    no client was set on, nothing changes. The mark lives on the session, not
    in a context variable, because FastAPI runs a request's dependencies and
    its endpoint on different threads while they share this one session.
    """

    def get_bind(self, mapper=None, clause=None, **kw):
        active = self.info.get(ACTIVE_CLIENT_KEY)
        if active is not None:
            tables = _tables_of(mapper, clause)
            if tables and tables <= _data_plane():
                return tenant_engine(active["dsn"], active["schema"])
            if tables & _data_plane() and tables - _data_plane():
                raise CrossPlaneQuery(
                    f"A single query cannot join {sorted(tables & _data_plane())} (client database) "
                    f"with {sorted(tables - _data_plane())} (central database)"
                )
        return super().get_bind(mapper=mapper, clause=clause, **kw)


class CrossPlaneQuery(RuntimeError):
    """A query mixes a client's own tables with central ones; split it in two."""


ACTIVE_CLIENT_KEY = "hunterai_data_client"
DEFAULT_CLIENT_KEY = "hunterai_default_data_client"

# The two places outside the central database where a client's data may live:
# its own Supabase project, or a schema of its agency's project.
OWN_DATABASE_MODES = ("supabase", "agency")


def store_of(client):
    """The record holding the connection to where ``client``'s data lives now
    (its own Supabase project, or its schema in the agency's), or None while it
    is in the central database. Both kinds answer to ``encrypted_dsn``,
    ``schema_name``, ``schema_version``, ``status`` and ``last_error``."""
    mode = getattr(client, "data_mode", "central") if client is not None else "central"
    if mode == "supabase":
        return client.data_store
    if mode == "agency":
        return client.agency_schema
    return None


class DataMoving(HTTPException):
    """The client's data is being moved between databases (a switch in
    progress): nothing may read or write it for the moment. Requests answer 503
    with Retry-After; webhooks are kept and replayed; sweeps skip the client."""

    def __init__(self, client_id=None):
        self.client_id = client_id
        super().__init__(
            status_code=503,
            detail="This client's data is being moved to another database. Try again in a minute.",
            headers={"Retry-After": "30"},
        )


def keep_for_replay(session: Session, client_id, source: str, payload: dict) -> None:
    """Store a webhook of a client whose data is moving; replayed after the move."""
    from .models import PendingInbound

    session.rollback()
    session.add(PendingInbound(client_id=client_id, source=source, payload=payload))
    session.commit()


def mark_unreachable(session: Session, client_id) -> None:
    """Record, for the panel, that a client's own database stopped answering."""
    from sqlalchemy import update

    from .models import ClientAgencySchema, ClientDataStore, now_utc

    session.rollback()
    for table in (ClientDataStore, ClientAgencySchema):
        session.execute(
            update(table).where(table.client_id == client_id).values(
                last_error="The client's database did not answer; messages are kept and will be processed when it is back",
                last_checked_at=now_utc(),
            )
        )
    session.commit()


def not_moving(session: Session, client_column):
    """A WHERE clause leaving out the rows of clients whose data is moving, for
    sweeps that must not touch a client mid-switch."""
    from sqlalchemy import select, true

    from .models import Client

    ids = moving_client_ids(session)
    if session.info.get(ACTIVE_CLIENT_KEY) is None:
        # Sweeping the central database: clients whose data lives in their own
        # are swept there, never here (defence in depth; they keep no rows here).
        ids += list(session.scalars(select(Client.id).where(Client.data_mode.in_(OWN_DATABASE_MODES))))
    return client_column.notin_(ids) if ids else true()


def moving_client_ids(session: Session) -> list:
    """Clients whose data is being moved right now (see DataMoving)."""
    from sqlalchemy import select

    from .models import Client

    return list(session.scalars(select(Client.id).where(Client.data_mode == "switching")))


def _data_plane() -> frozenset[str]:
    from .data_plane import DATA_PLANE

    return DATA_PLANE


def _tables_of(mapper, clause) -> set[str]:
    from sqlalchemy.sql import util as sql_util

    names: set[str] = set()
    if clause is not None:
        try:
            names = {t.name for t in sql_util.find_tables(clause, include_crud=True, include_joins=True) if hasattr(t, "name")}
        except Exception:  # noqa: BLE001 - an exotic construct falls back to the mapper
            names = set()
    if not names and mapper is not None:
        names = {mapper.local_table.name}
    return names


SessionLocal = sessionmaker(bind=engine, class_=RoutingSession, autoflush=False, expire_on_commit=False)


def new_session():
    """Open a session. The one place a session is created.

    Request handlers receive one through ``get_db``, which FastAPI lets a
    deployment substitute; the test suite does exactly that. Work that runs
    outside a request has no dependency to receive, so it calls this instead of
    reaching for ``SessionLocal`` directly.

    Both paths going through here is the point: a substituted session reaches
    every query, not only the routed ones. Calling ``SessionLocal()`` from
    elsewhere silently opts that code out, and the resulting failure surfaces
    inside whatever background task made the call rather than in a response.
    """
    return SessionLocal()


def get_db():
    db = new_session()
    try:
        yield db
    finally:
        db.close()


# A client's own database (phase B). The data-plane tables of a client whose
# data lives in its Supabase are read and written there; everything else stays
# on the central engine. Engines are cached per connection string and kept
# small, since there is one per connected client.

_tenant_engines: dict[str, object] = {}
_tenant_lock = threading.Lock()


def tenant_engine(dsn: str, schema: str):
    """An engine for a client's own database, pinned to ``schema``.

    The schema is set with ``SET LOCAL`` at the start of every transaction
    rather than as a connection option, because Supabase's transaction pooler
    hands each transaction whatever server connection is free."""
    key = hashlib.sha256(f"{schema}\n{dsn}".encode()).hexdigest()
    with _tenant_lock:
        cached = _tenant_engines.get(key)
        if cached is not None:
            return cached
        url = dsn.replace("postgresql://", "postgresql+psycopg://", 1) if dsn.startswith("postgresql://") else dsn
        created = create_engine(
            url,
            pool_size=2,
            max_overflow=1,
            pool_pre_ping=True,
            pool_recycle=240,
            # The transaction pooler cannot keep prepared statements between transactions.
            connect_args={**KEEPALIVE_CONNECT_ARGS, "prepare_threshold": None},
        )
        quoted = '"' + schema.replace('"', '""') + '"'

        @event.listens_for(created, "begin")
        def _pin_schema(connection):
            connection.exec_driver_sql(f"SET LOCAL search_path TO {quoted}")

        _tenant_engines[key] = created
        return created


def data_plane_mappers() -> list:
    from .data_plane import DATA_PLANE

    return [mapper.class_ for mapper in Base.registry.mappers if mapper.local_table.name in DATA_PLANE]


def use_client(session: Session, client) -> None:
    """Mark ``session`` as working on ``client``: from here on its data-plane
    tables go to the client's own database when it has one in use. Call it
    right after resolving the client, before touching its conversations,
    contacts or messages. A central client clears the mark."""
    if client is not None and getattr(client, "data_mode", "central") == "switching":
        raise DataMoving(client.id)
    store = store_of(client)
    if store is None or not store.encrypted_dsn:
        session.info.pop(ACTIVE_CLIENT_KEY, None)
        # Only the test suite sets a default (sessions it opens itself; see conftest).
        if DEFAULT_CLIENT_KEY in session.info:
            session.info[ACTIVE_CLIENT_KEY] = dict(session.info[DEFAULT_CLIENT_KEY])
        return
    from .services.tenant_schema import head

    if store.schema_version != head():
        # Its tables are older than this release expects: nothing may touch
        # them until the boot-time update brings them forward (webhooks are
        # kept and replayed then).
        raise DataMoving(client.id)
    from .security import decrypt_secret

    session.info[ACTIVE_CLIENT_KEY] = {
        "client_id": client.id, "dsn": decrypt_secret(store.encrypted_dsn), "schema": store.schema_name,
    }


def new_client_session(client_id):
    """``new_session()`` already working on one client (see ``use_client``), for
    background work that starts from a client's id rather than a request."""
    db = new_session()
    if client_id is not None:
        from .models import Client

        use_client(db, db.get(Client, client_id))
    return db


def own_database_clients(session: Session, *, agency_id=None, client_id=None) -> list:
    """Clients whose data lives outside the central database right now, in
    their own project or the agency's (optionally of one agency, or just one
    client)."""
    from sqlalchemy import select

    from .models import Client

    query = select(Client).where(Client.data_mode.in_(OWN_DATABASE_MODES))
    if agency_id is not None:
        query = query.where(Client.agency_id == agency_id)
    if client_id is not None:
        query = query.where(Client.id == client_id)
    return list(session.scalars(query))


def find_across_databases(session: Session, lookup, *, agency_id, client_id=None):
    """``lookup()`` in the central database, then in each own database of the
    agency's clients, until it returns something. For routes that receive a
    row's id without its client (a conversation id in the agency panel). The
    session is left pointed at the database where the row was found."""
    use_client(session, None)
    found = lookup()
    if found is not None:
        return found
    for client in own_database_clients(session, agency_id=agency_id, client_id=client_id):
        use_client(session, client)
        found = lookup()
        if found is not None:
            return found
    use_client(session, None)
    return None


def each_database(session: Session, *, agency_id=None, client_id=None, on_unavailable=None):
    """Point ``session`` at every database holding client data in turn: the
    central one, then each client's own (optionally only one agency's, or one
    client's). For sweeps over the data plane (idle conversations, due
    replies) and for agency-wide lists, which read each database and merge.
    The session is committed and emptied between databases so nothing read
    from one is written to another; yields the client (None for central).
    Anything built from a pass must be finished (serialized) inside it.

    A client whose data is being moved, or whose own database is not reachable
    yet, is left out and the sweep carries on, so one half-migrated client does
    not empty a list that spans the whole agency. ``on_unavailable`` is called
    with that client, if given, so the caller can say the answer is partial
    rather than letting it pass as a complete one.

    With ``client_id`` the caller named one client and asked about it: there is
    nothing to carry on to, so the error is raised as before. "No conversations"
    and "could not read them" are different answers and only the second one is
    true here.
    """
    from .models import Client

    ids = [client.id for client in own_database_clients(session, agency_id=agency_id, client_id=client_id)]
    for client_ref in [None, *ids]:
        session.commit()
        session.expunge_all()
        # Reloaded after emptying the session, so the client is attached.
        client = session.get(Client, client_ref) if client_ref is not None else None
        try:
            use_client(session, client)
        except DataMoving:
            if client is None or client_id is not None:
                raise
            # The agency-wide sweep: this client's leads are missing, and the
            # ones that can be read are still worth answering with.
            if on_unavailable is not None:
                on_unavailable(client)
            continue
        yield client
    session.commit()
    session.expunge_all()
    use_client(session, None)


def active_client_id(session: Session):
    active = session.info.get(ACTIVE_CLIENT_KEY)
    return active["client_id"] if active else None
