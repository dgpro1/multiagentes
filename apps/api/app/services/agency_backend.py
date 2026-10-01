"""The agency's own Supabase project, and a schema for each of its clients in it.

An agency administrator connects their Supabase project once (OAuth with PKCE,
the same flow a client's own project uses). From then on the agency can take a
client's data into that project (``tenant_switch.to_agency``): this module
creates the client's place there, which is a schema of its own and a database
role that may use that schema and nothing else, so one client's connection can
never read another client's rows even if the application had a bug.

Nothing here keeps a database login for the agency: the role of each client is
created through the Management API, with a generated password, and only that
role's connection string is stored, encrypted, on ``ClientAgencySchema``.
Disconnecting forgets the OAuth grant and never touches the agency's project.
"""

import asyncio
import hashlib
import logging
import re
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import agency_features
from ..config import get_settings
from ..models import (
    Agency,
    AgencyDataStore,
    AgencyStorageConnection,
    Client,
    ClientAgencySchema,
    ClientStorageConnection,
    DataStoreOAuthState,
    now_utc,
)
from ..schemas_resources import StorageConnect
from ..security import decrypt_secret, encrypt_secret
from . import data_store, resource_storage as storage, supabase_mgmt as supabase, tenant_schema

logger = logging.getLogger(__name__)

MODULE = "agency_backend"
STATE_MINUTES = 10
_REF = re.compile(r"^[a-z0-9]{20}$")


def schema_name_for(client_id) -> str:
    """The schema and the role of a client share one name: derived from its id,
    lowercase and short enough for a Postgres identifier."""
    return f"hunterai_c_{client_id.hex[:20]}"


def ensure_module(agency: Agency) -> None:
    agency_features.ensure_enabled(agency, MODULE)


def get_or_create(db: Session, agency: Agency) -> AgencyDataStore:
    store = db.scalar(select(AgencyDataStore).where(AgencyDataStore.agency_id == agency.id))
    if store:
        return store
    store = AgencyDataStore(agency_id=agency.id)
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


def _clients_in_agency_mode(db: Session, agency_id) -> int:
    return db.scalar(
        select(func.count()).select_from(Client).where(Client.agency_id == agency_id, Client.data_mode == "agency")
    ) or 0


def out(db: Session, agency: Agency) -> dict:
    store = db.scalar(select(AgencyDataStore).where(AgencyDataStore.agency_id == agency.id))
    base = {
        "module_enabled": agency_features.is_enabled(agency, MODULE),
        "oauth_ready": supabase.configured(),
        "recommended_region": get_settings().supabase_recommended_region,
        "clients_in_agency": _clients_in_agency_mode(db, agency.id),
    }
    if not store:
        return {**base, "status": "none"}
    return {
        **base,
        "status": store.status,
        "project_ref": store.project_ref,
        "project_name": store.project_name,
        "region": store.region,
        "last_error": store.last_error,
        "last_checked_at": store.last_checked_at,
        "connected_at": store.connected_at,
    }


def ready(db: Session, agency: Agency) -> bool:
    """Whether a client of this agency may be moved into the agency's project."""
    store = db.scalar(select(AgencyDataStore).where(AgencyDataStore.agency_id == agency.id))
    return bool(store and store.status == "connected" and agency_features.is_enabled(agency, MODULE))


# Connecting the project (an agency administrator, signed in).

def start_connection(db: Session, agency: Agency) -> str:
    ensure_module(agency)
    if not supabase.configured():
        raise HTTPException(status_code=503, detail="Supabase is not configured on this server yet")
    store = get_or_create(db, agency)
    db.execute(delete(DataStoreOAuthState).where(DataStoreOAuthState.expires_at < now_utc() - timedelta(days=1)))
    raw, verifier = secrets.token_urlsafe(32), supabase.new_verifier()
    db.add(DataStoreOAuthState(
        id=hashlib.sha256(raw.encode()).hexdigest(),
        agency_data_store_id=store.id,
        connect_token="agency",
        encrypted_verifier=encrypt_secret(verifier),
        expires_at=now_utc() + timedelta(minutes=STATE_MINUTES),
    ))
    db.commit()
    return supabase.authorization_url(raw, verifier)


async def finish_connection(db: Session, state: DataStoreOAuthState, code: str | None, error: str | None) -> str:
    """Where to send the administrator's browser back to, with the outcome in the query."""
    back = f"{get_settings().frontend_url.rstrip('/')}/settings"
    store = db.get(AgencyDataStore, state.agency_data_store_id)
    if not store:
        return f"{back}?backend=expired"
    if error or not code:
        return f"{back}?backend=denied"
    try:
        grant = await supabase.exchange_code(code, decrypt_secret(state.encrypted_verifier))
    except supabase.SupabaseError:
        return f"{back}?backend=error"
    data_store._keep_grant(store, grant)
    if store.status != "connected":
        store.status = "authorized"
    store.last_error = None
    db.commit()
    return f"{back}?backend=authorized"


async def projects(db: Session, agency: Agency) -> list[dict]:
    ensure_module(agency)
    store = get_or_create(db, agency)
    try:
        return await supabase.list_projects(await data_store._access_token(db, store))
    except supabase.SupabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def choose_project(db: Session, agency: Agency, ref: str) -> AgencyDataStore:
    ensure_module(agency)
    if not _REF.match(ref or ""):
        raise HTTPException(status_code=422, detail="That is not a Supabase project reference")
    store = get_or_create(db, agency)
    if store.status == "connected" and store.project_ref and store.project_ref != ref and _clients_in_agency_mode(db, agency.id):
        raise HTTPException(status_code=409, detail="Move the agency's clients out of the connected project before choosing another one")
    access = await data_store._access_token(db, store)
    try:
        listed = {p["ref"]: p for p in await supabase.list_projects(access)}
        if ref not in listed:
            raise HTTPException(status_code=404, detail="That project is not among the ones this authorization can see")
        await supabase.run_query(access, ref, "select 1")
    except supabase.SupabaseError as exc:
        store.status, store.last_error = "error", str(exc)
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    store.project_ref = ref
    store.project_name = listed[ref]["name"][:255]
    store.region = listed[ref]["region"][:40]
    store.status = "connected"
    store.last_error = None
    store.last_checked_at = store.connected_at = now_utc()
    db.commit()
    db.refresh(store)
    return store


async def recheck(db: Session, agency: Agency) -> AgencyDataStore:
    ensure_module(agency)
    store = db.scalar(select(AgencyDataStore).where(AgencyDataStore.agency_id == agency.id))
    if not store or not store.project_ref:
        raise HTTPException(status_code=409, detail="This agency has not connected a Supabase project")
    try:
        await supabase.run_query(await data_store._access_token(db, store), store.project_ref, "select 1")
        store.status, store.last_error = "connected", None
    except supabase.SupabaseError:
        store.status = "error"
        store.last_error = "Could not reach the Supabase project (paused, deleted or the authorization was revoked?)"
    store.last_checked_at = now_utc()
    db.commit()
    db.refresh(store)
    return store


def disconnect(db: Session, agency: Agency) -> None:
    """Forget the OAuth grant. The agency's project and everything in it stay as they are."""
    store = db.scalar(select(AgencyDataStore).where(AgencyDataStore.agency_id == agency.id))
    if not store:
        return
    if _clients_in_agency_mode(db, agency.id):
        # Their conversations live there: forgetting the grant would cut them off from moving back out.
        raise HTTPException(status_code=409, detail="Move the agency's clients out of the connected project before disconnecting it")
    store.encrypted_refresh_token = store.encrypted_access_token = None
    store.access_token_expires_at = None
    store.project_ref = store.project_name = store.region = ""
    store.status = "pending"
    store.last_error = None
    store.connected_at = None
    db.commit()


# A client's place in the project.

def provisioning_sql(name: str, password: str) -> str:
    """Creates (or re-keys) the client's role and gives it a schema of the same
    name. Nobody else is granted anything on that schema, and ``public`` loses
    its default access to it, so no other role of the project reads it. The
    password is generated here from [0-9a-f] and the name from a uuid, so
    nothing needs quoting."""
    assert re.fullmatch(r"[0-9a-f]{48}", password) and re.fullmatch(r"hunterai_c_[0-9a-f]{20}", name)
    return f"""
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{name}') THEN
    CREATE ROLE {name} LOGIN PASSWORD '{password}';
  ELSE
    ALTER ROLE {name} WITH LOGIN PASSWORD '{password}';
  END IF;
END
$$;
CREATE SCHEMA IF NOT EXISTS {name};
REVOKE ALL ON SCHEMA {name} FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA {name} TO {name};
ALTER ROLE {name} SET search_path = {name};
"""


def _probe_existing(row: ClientAgencySchema) -> bool:
    if not row.encrypted_dsn:
        return False
    try:
        data_store.probe_dsn(decrypt_secret(row.encrypted_dsn))
        return True
    except Exception:  # noqa: BLE001 - a dead credential is simply re-issued
        return False


async def provision_client(db: Session, client: Client) -> ClientAgencySchema:
    """The client's schema in the agency's project, ready: role created, login
    proven, tables up to date. Safe to repeat: a working one is kept, a dead
    one gets a new password."""
    agency = db.get(Agency, client.agency_id)
    ensure_module(agency)
    store = db.scalar(select(AgencyDataStore).where(AgencyDataStore.agency_id == client.agency_id))
    if not store or store.status != "connected":
        raise HTTPException(status_code=409, detail="Connect the agency's Supabase project first")
    name = schema_name_for(client.id)
    row = client.agency_schema
    if row is None:
        row = ClientAgencySchema(agency_id=client.agency_id, client_id=client.id, schema_name=name, role_name=name)
        client.agency_schema = row
        db.add(row)
        db.commit()
        db.refresh(row)
    if not await asyncio.to_thread(_probe_existing, row):
        access = await data_store._access_token(db, store)
        password = secrets.token_hex(24)
        try:
            await supabase.run_query(access, store.project_ref, provisioning_sql(name, password))
            dsn = data_store.build_dsn(store.project_ref, await supabase.pooler_config(access, store.project_ref), password, role=name)
        except supabase.SupabaseError as exc:
            row.status, row.last_error = "error", str(exc)
            db.commit()
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        try:
            row.db_size_bytes = await asyncio.to_thread(data_store.probe_dsn, dsn)
        except Exception as exc:  # noqa: BLE001 - any login failure is reported, never the DSN
            logger.warning("Agency schema probe failed for client %s: %s", client.id, type(exc).__name__)
            row.status = "error"
            row.last_error = "The role was created but logging in through the pooler failed; try again in a minute"
            db.commit()
            raise HTTPException(status_code=502, detail=row.last_error) from exc
        row.encrypted_dsn = encrypt_secret(dsn)
        row.schema_version = ""
    row.status = "connected"
    row.last_error = None
    row.last_checked_at = row.connected_at = now_utc()
    db.commit()
    if row.schema_version != tenant_schema.head():
        tenant_schema.upgrade_store(db, row)
    db.refresh(row)
    return row


# The agency's R2 bucket.

def _storage(db: Session, agency: Agency) -> AgencyStorageConnection | None:
    return db.scalar(select(AgencyStorageConnection).where(AgencyStorageConnection.agency_id == agency.id))


def _clients_hosted(db: Session, agency_id) -> int:
    return db.scalar(
        select(func.count()).select_from(ClientStorageConnection)
        .where(ClientStorageConnection.agency_id == agency_id, ClientStorageConnection.hosted_by == "agency")
    ) or 0


def storage_ready(db: Session, agency: Agency) -> bool:
    """Whether a client of this agency may be moved into the agency's bucket."""
    conn = _storage(db, agency)
    return bool(conn and conn.status == "connected" and agency_features.is_enabled(agency, MODULE))


def storage_out(db: Session, agency: Agency) -> dict:
    conn = _storage(db, agency)
    base = {"module_enabled": agency_features.is_enabled(agency, MODULE), "clients_hosted": _clients_hosted(db, agency.id)}
    if not conn:
        return {**base, "status": "none"}
    hint = ""
    if conn.encrypted_access_key_id:
        try:
            hint = "\u2022\u2022\u2022\u2022" + decrypt_secret(conn.encrypted_access_key_id)[-4:]
        except Exception:  # noqa: BLE001 - a key that no longer decrypts is reported by status
            hint = ""
    return {
        **base,
        "status": conn.status,
        "account_id": conn.account_ref,
        "bucket": conn.bucket,
        "access_key_hint": hint,
        "last_error": conn.last_error,
        "last_checked_at": conn.last_checked_at,
        "connected_at": conn.connected_at,
    }


def storage_connect(db: Session, agency: Agency, payload: StorageConnect) -> AgencyStorageConnection:
    """Probe the credentials and keep them only if the bucket takes a write, a read and a delete."""
    ensure_module(agency)
    if not storage.valid_account_id(payload.account_id):
        raise HTTPException(status_code=422, detail="The Cloudflare account id must be 32 hexadecimal characters")
    if not storage.valid_bucket(payload.bucket):
        raise HTTPException(status_code=422, detail="That is not a valid bucket name")
    conn = _storage(db, agency)
    if conn is None:
        conn = AgencyStorageConnection(agency_id=agency.id)
        db.add(conn)
        db.commit()
        db.refresh(conn)
    if conn.status == "connected" and conn.bucket and (conn.bucket != payload.bucket or conn.account_ref != payload.account_id) and _clients_hosted(db, agency.id):
        raise HTTPException(status_code=409, detail="Move the agency's clients' files out of the connected bucket before choosing another one")
    try:
        storage.Storage(payload.account_id, payload.access_key_id, payload.secret_access_key, payload.bucket).probe()
    except storage.StorageError as exc:
        conn.last_error = str(exc)
        conn.last_checked_at = now_utc()
        if conn.status != "connected":
            conn.status = "error"
        db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    conn.account_ref = payload.account_id
    conn.bucket = payload.bucket
    conn.encrypted_access_key_id = encrypt_secret(payload.access_key_id)
    conn.encrypted_secret = encrypt_secret(payload.secret_access_key)
    conn.status = "connected"
    conn.last_error = None
    conn.last_checked_at = conn.connected_at = now_utc()
    db.commit()
    db.refresh(conn)
    return conn


def storage_recheck(db: Session, agency: Agency) -> AgencyStorageConnection:
    ensure_module(agency)
    conn = _storage(db, agency)
    if not conn or not conn.encrypted_access_key_id:
        raise HTTPException(status_code=409, detail="This agency has not connected a Cloudflare bucket")
    try:
        storage.Storage(
            conn.account_ref, decrypt_secret(conn.encrypted_access_key_id), decrypt_secret(conn.encrypted_secret or ""),
            conn.bucket, conn.region,
        ).probe()
        conn.status, conn.last_error = "connected", None
    except storage.StorageError as exc:
        conn.status, conn.last_error = "error", str(exc)
    conn.last_checked_at = now_utc()
    db.commit()
    db.refresh(conn)
    return conn


def storage_disconnect(db: Session, agency: Agency) -> None:
    """Forget the credentials. The files stay in the agency's bucket."""
    conn = _storage(db, agency)
    if not conn:
        return
    if _clients_hosted(db, agency.id):
        raise HTTPException(status_code=409, detail="Move the agency's clients' files out of the connected bucket before disconnecting it")
    conn.encrypted_access_key_id = conn.encrypted_secret = None
    conn.account_ref = conn.bucket = ""
    conn.status = "pending"
    conn.last_error = None
    conn.connected_at = None
    db.commit()
