"""The client's own Supabase project: connecting, checking and disconnecting it.

Everything the customer does happens on a public link (``/connect/supabase/{token}``)
so the business owner needs no OpenLivery account, the way Google Calendar
connects: authorize with Supabase (OAuth with PKCE), pick a project, and
OpenLivery provisions itself there. Provisioning runs SQL through the
Management API to create the ``hunterai_app`` login role with a generated
password and a ``hunterai`` schema it may use, then builds that role's
connection string from the project's pooler settings and proves it with a
real login. The API never reveals the project's own password, and OpenLivery
never needs it.

Nothing is migrated here: this is the connector. Which data lives in that
database, and when, is decided per client later (phase B, see work/).
"""

import asyncio
import hashlib
import logging
import re
import secrets
from datetime import timedelta
from urllib.parse import quote, urlencode

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Agency, Client, ClientDataStore, DataStoreOAuthState, new_public_id, now_utc
from ..security import decrypt_secret, encrypt_secret
from . import supabase_mgmt as supabase

logger = logging.getLogger(__name__)

# Named after the brand the customer sees in their own Supabase project.
ROLE = "hunterai_app"
SCHEMA = "hunterai"
STATE_MINUTES = 10
_REF = re.compile(r"^[a-z0-9]{20}$")
_ALLOWED_HOSTS = (".pooler.supabase.com", ".supabase.co")


def _frontend() -> str:
    return get_settings().frontend_url.rstrip("/")


def connect_url(store: ClientDataStore) -> str:
    return f"{_frontend()}/connect/supabase/{store.connect_token}"


def _link_expiry():
    return now_utc() + timedelta(days=get_settings().datastore_link_days)


def get_or_create(db: Session, client: Client) -> ClientDataStore:
    if client.data_store:
        return client.data_store
    store = ClientDataStore(agency_id=client.agency_id, client_id=client.id, connect_expires_at=_link_expiry())
    client.data_store = store
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


def out(client: Client) -> dict:
    store = client.data_store
    region = get_settings().supabase_recommended_region
    from .tenant_schema import head

    if not store:
        return {"status": "none", "oauth_ready": supabase.configured(), "link_active": False,
                "recommended_region": region, "data_mode": client.data_mode, "schema_head": head()}
    return {
        "status": store.status,
        "data_mode": client.data_mode,
        "schema_version": store.schema_version,
        "schema_head": head(),
        "oauth_ready": supabase.configured(),
        "recommended_region": region,
        "project_ref": store.project_ref,
        "project_name": store.project_name,
        "region": store.region,
        "db_size_bytes": store.db_size_bytes,
        "last_error": store.last_error,
        "last_checked_at": store.last_checked_at,
        "connected_at": store.connected_at,
        "link_active": store.connect_expires_at > now_utc(),
    }


def renew_link(db: Session, client: Client) -> ClientDataStore:
    """A fresh link; the previous one, and any authorization it started, stop working."""
    store = get_or_create(db, client)
    store.connect_token = new_public_id()
    store.connect_expires_at = _link_expiry()
    db.execute(delete(DataStoreOAuthState).where(DataStoreOAuthState.data_store_id == store.id))
    db.commit()
    db.refresh(store)
    return store


def disconnect(db: Session, client: Client) -> None:
    """Forget every credential. The customer's database and its data are left as they are."""
    store = client.data_store
    if not store:
        return
    if client.data_mode != "central":
        # Its conversations live there: forgetting the credentials would cut the client off from them.
        raise HTTPException(status_code=409, detail="Move the client's data back to HunterAI before disconnecting its database")
    store.encrypted_refresh_token = store.encrypted_access_token = store.encrypted_dsn = None
    store.access_token_expires_at = None
    store.project_ref = store.project_name = store.region = ""
    store.db_size_bytes = None
    store.status = "pending"
    store.last_error = None
    store.connected_at = None
    db.commit()


# The public link.

def by_link(db: Session, token: str) -> ClientDataStore:
    store = db.scalar(select(ClientDataStore).where(ClientDataStore.connect_token == token)) if token else None
    if not store or store.connect_expires_at <= now_utc():
        raise HTTPException(status_code=404, detail="This link is not valid or has expired")
    return store


def link_info(db: Session, token: str) -> dict:
    store = by_link(db, token)
    agency = db.get(Agency, store.agency_id)
    return {
        "client_name": store.client.name,
        "agency_name": agency.name if agency else "",
        "status": store.status,
        "oauth_ready": supabase.configured(),
        "recommended_region": get_settings().supabase_recommended_region,
        "project_name": store.project_name,
        "project_ref": store.project_ref,
        "last_error": store.last_error,
        "expires_at": store.connect_expires_at,
    }


def start_connection(db: Session, token: str) -> str:
    store = by_link(db, token)
    if not supabase.configured():
        raise HTTPException(status_code=503, detail="Supabase is not configured on this server yet")
    db.execute(delete(DataStoreOAuthState).where(DataStoreOAuthState.expires_at < now_utc() - timedelta(days=1)))
    raw, verifier = secrets.token_urlsafe(32), supabase.new_verifier()
    db.add(DataStoreOAuthState(
        id=hashlib.sha256(raw.encode()).hexdigest(),
        data_store_id=store.id,
        connect_token=store.connect_token,
        encrypted_verifier=encrypt_secret(verifier),
        expires_at=now_utc() + timedelta(minutes=STATE_MINUTES),
    ))
    db.commit()
    return supabase.authorization_url(raw, verifier)


async def finish_connection(db: Session, raw_state: str, code: str | None, error: str | None) -> str:
    """Where to send the browser back to, with the outcome in the query."""
    state = db.scalar(
        select(DataStoreOAuthState)
        .where(DataStoreOAuthState.id == hashlib.sha256(raw_state.encode()).hexdigest())
        .with_for_update()
    ) if raw_state and len(raw_state) <= 256 else None
    if not state or state.used_at or state.expires_at <= now_utc():
        return f"{_frontend()}/connect/supabase/expired?result=expired"
    state.used_at = now_utc()
    db.commit()
    back = f"{_frontend()}/connect/supabase/{state.connect_token}"
    store = db.get(ClientDataStore, state.data_store_id)
    if not store or store.connect_token != state.connect_token or store.connect_expires_at <= now_utc():
        return f"{back}?result=expired"
    if error or not code:
        return f"{back}?result=denied"
    try:
        grant = await supabase.exchange_code(code, decrypt_secret(state.encrypted_verifier))
    except supabase.SupabaseError:
        return f"{back}?result=error"
    _keep_grant(store, grant)
    if store.status != "connected":
        store.status = "authorized"
    store.last_error = None
    db.commit()
    return f"{back}?result=authorized"


def _keep_grant(store: ClientDataStore, grant: supabase.Grant) -> None:
    store.encrypted_access_token = encrypt_secret(grant.access_token)
    store.encrypted_refresh_token = encrypt_secret(grant.refresh_token)
    store.access_token_expires_at = grant.expires_at


async def _access_token(db: Session, store: ClientDataStore) -> str:
    if not store.encrypted_refresh_token:
        raise HTTPException(status_code=409, detail="Authorize with Supabase first")
    if store.encrypted_access_token and store.access_token_expires_at and store.access_token_expires_at > now_utc():
        return decrypt_secret(store.encrypted_access_token)
    try:
        grant = await supabase.refresh(decrypt_secret(store.encrypted_refresh_token))
    except supabase.SupabaseError as exc:
        store.status = "error"
        store.last_error = "Supabase no longer accepts this authorization; connect again"
        db.commit()
        raise HTTPException(status_code=409, detail=store.last_error) from exc
    _keep_grant(store, grant)
    db.commit()
    return grant.access_token


async def projects_by_link(db: Session, token: str) -> list[dict]:
    store = by_link(db, token)
    try:
        return await supabase.list_projects(await _access_token(db, store))
    except supabase.SupabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def provisioning_sql(password: str) -> str:
    """Creates (or re-keys) our own role and gives it a schema to work in.

    A schema of that name that already holds objects someone else owns is
    refused, never adopted: the customer's project may already run another
    application there. The password is generated here from [0-9a-f], so
    nothing needs quoting."""
    assert re.fullmatch(r"[0-9a-f]{48}", password)
    return f"""
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = '{SCHEMA}'
      AND c.relowner <> COALESCE((SELECT oid FROM pg_roles WHERE rolname = '{ROLE}'), 0)
  ) THEN
    RAISE EXCEPTION 'The schema {SCHEMA} already exists in this project with objects that are not HunterAI''s';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
    CREATE ROLE {ROLE} LOGIN PASSWORD '{password}';
  ELSE
    ALTER ROLE {ROLE} WITH LOGIN PASSWORD '{password}';
  END IF;
END
$$;
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
GRANT USAGE, CREATE ON SCHEMA {SCHEMA} TO {ROLE};
ALTER ROLE {ROLE} SET search_path = {SCHEMA};
"""


def build_dsn(ref: str, pooler: dict, password: str) -> str:
    host = str(pooler.get("db_host") or "").strip().lower()
    if not host.endswith(_ALLOWED_HOSTS):
        raise HTTPException(status_code=502, detail="Supabase returned an unexpected database host")
    port = int(pooler.get("db_port") or 6543)
    name = str(pooler.get("db_name") or "postgres")
    # The shared pooler routes by "role.project"; a direct host takes the bare role.
    user = f"{ROLE}.{ref}" if host.endswith(".pooler.supabase.com") else ROLE
    return f"postgresql://{quote(user)}:{password}@{host}:{port}/{quote(name)}?{urlencode({'sslmode': 'require'})}"


def probe_dsn(dsn: str) -> int:
    """Log in as OpenLivery's role and read the database size. Raises on failure."""
    import psycopg

    # The transaction pooler cannot keep prepared statements between transactions.
    with psycopg.connect(dsn, connect_timeout=10, prepare_threshold=None) as conn:
        size = conn.execute("select pg_database_size(current_database())").fetchone()[0]
    return int(size)


async def choose_project(db: Session, token: str, ref: str) -> ClientDataStore:
    store = by_link(db, token)
    if not _REF.match(ref or ""):
        raise HTTPException(status_code=422, detail="That is not a Supabase project reference")
    access = await _access_token(db, store)
    try:
        projects = {p["ref"]: p for p in await supabase.list_projects(access)}
        if ref not in projects:
            raise HTTPException(status_code=404, detail="That project is not among the ones this authorization can see")
        password = secrets.token_hex(24)
        await supabase.run_query(access, ref, provisioning_sql(password))
        dsn = build_dsn(ref, await supabase.pooler_config(access, ref), password)
    except supabase.SupabaseError as exc:
        store.status, store.last_error = "error", str(exc)
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        size = await asyncio.to_thread(probe_dsn, dsn)
    except Exception as exc:  # noqa: BLE001 - any login failure is reported, never the DSN
        logger.warning("Supabase probe failed for client %s: %s", store.client_id, type(exc).__name__)
        store.status = "error"
        store.last_error = "The role was created but logging in through the pooler failed; try again in a minute"
        db.commit()
        raise HTTPException(status_code=502, detail=store.last_error) from exc
    store.project_ref = ref
    store.project_name = projects[ref]["name"][:255]
    store.region = projects[ref]["region"][:40]
    store.encrypted_dsn = encrypt_secret(dsn)
    store.db_size_bytes = size
    store.status = "connected"
    store.last_error = None
    store.last_checked_at = store.connected_at = now_utc()
    db.commit()
    db.refresh(store)
    return store


async def recheck(db: Session, client: Client) -> ClientDataStore:
    store = client.data_store
    if not store or not store.encrypted_dsn:
        raise HTTPException(status_code=409, detail="This client has not connected a Supabase project")
    try:
        store.db_size_bytes = await asyncio.to_thread(probe_dsn, decrypt_secret(store.encrypted_dsn))
        store.status, store.last_error = "connected", None
    except Exception:  # noqa: BLE001
        store.status, store.last_error = "error", "Could not log in to the Supabase database (paused project or revoked role?)"
    store.last_checked_at = now_utc()
    db.commit()
    db.refresh(store)
    return store
