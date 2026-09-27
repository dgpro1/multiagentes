import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://openlivery:openlivery@localhost:5432/openlivery_test",
)
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("WHATSAPP_INTERNAL_API", "true")
os.environ.setdefault("SOCIAL_WORKER_ENABLED", "false")
os.environ.setdefault("MESSAGING_PROVIDER_API_KEY", "test-provider-key")
os.environ.setdefault("MESSAGING_PROVIDER_WEBHOOK_SECRET", "test-webhook-secret")
# Never touch the real provider from tests: service seams are patched per
# test, and anything unpatched fails fast on a closed loopback port.
os.environ.setdefault("MESSAGING_PROVIDER_BASE_URL", "http://127.0.0.1:9")

from app.database import Base, RoutingSession, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import whatsapp_inbound  # noqa: E402


test_engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
# Sessions the application's routes get (through the get_db override).
RouteSession = sessionmaker(bind=test_engine, class_=RoutingSession, autoflush=False, expire_on_commit=False)
# Sessions tests open themselves to seed or inspect rows. Under
# HUNTERAI_TENANT_TESTS=1 they point at the clients' database by default (the
# rows a test seeds live where its clients keep their data); routes never get
# that default, so a route that forgets use_client() still fails.
_DIRECT_TENANT: dict = {}


class _TestingSession(RoutingSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if _DIRECT_TENANT:
            from app.database import ACTIVE_CLIENT_KEY, DEFAULT_CLIENT_KEY

            self.info[ACTIVE_CLIENT_KEY] = dict(_DIRECT_TENANT)
            self.info[DEFAULT_CLIENT_KEY] = dict(_DIRECT_TENANT)


TestingSession = sessionmaker(bind=test_engine, class_=_TestingSession, autoflush=False, expire_on_commit=False)

# HUNTERAI_TENANT_TESTS=1 runs the suite with every client keeping its data in
# its own database (a throwaway schema of the test database per test), so any
# route that forgets use_client() or joins across the two databases fails.
TENANT_TESTS = os.environ.get("HUNTERAI_TENANT_TESTS") == "1"

if TENANT_TESTS:
    # A pytest run killed mid-test (timeout, crash) leaves the central test
    # schema half-built and the per-test tenant schemas orphaned, and the next
    # run's create_all then dies on a leftover pg_type entry. The test database
    # holds nothing worth keeping, so tenant runs always start from a slate.
    import psycopg

    _conn = psycopg.connect(os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://"), autocommit=True)
    _cur = _conn.cursor()
    _cur.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname = current_database() and pid <> pg_backend_pid()")
    _cur.execute("select nspname from pg_namespace where nspname like 'hunterai_tt_%'")
    for (_orphan,) in _cur.fetchall():
        _cur.execute(f'drop schema "{_orphan}" cascade')
    _cur.execute("drop schema public cascade")
    _cur.execute("create schema public")
    _conn.close()


def override_get_db():
    db = RouteSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def immediate_replies(monkeypatch):
    """Answer inbound WhatsApp messages synchronously, whatever the agent's delay.

    The reply delay is a per-agent setting with a non-zero default, and most
    tests want the reply back in the inbound response. Tests of the delayed
    path patch a short delay of their own on top of this.
    """
    monkeypatch.setattr(whatsapp_inbound, "reply_delay_seconds", lambda agent: 0.0)


@pytest.fixture(autouse=True)
def fixed_model_catalog():
    """A known catalog, so no test reads OpenRouter and the prices the cost
    reports are asserted against stay put."""
    from app.services import model_catalog as catalog

    chat = [
        catalog.ModelInfo("openai/gpt-5.6-luna", "openai", "GPT-5.6 Luna", "openai", 1_050_000, 128_000, True, True, 0.0002, 0.0012),
        catalog.ModelInfo("openai/gpt-5.6-sol", "openai", "GPT-5.6 Sol", "openai", 1_050_000, 128_000, True, True, 0.001, 0.004),
        catalog.ModelInfo("openai/gpt-4.1", "openai", "GPT-4.1", "openai", 1_047_576, 32_768, True, True, 0.002, 0.008),
        catalog.ModelInfo("deepseek/deepseek-v4-flash", "deepseek", "DeepSeek V4 Flash", "deepseek", 128_000, 8_192, True, False, 0.0001, 0.0002),
        catalog.ModelInfo("anthropic/claude-sonnet-5", "anthropic", "Claude Sonnet 5", "anthropic", 1_000_000, 64_000, True, True, 0.003, 0.015),
    ]
    embeddings = [
        catalog.EmbeddingModelInfo("openai/text-embedding-3-small", "openai", "text-embedding-3-small", 8_192, 0.00002),
        catalog.EmbeddingModelInfo("openai/text-embedding-3-large", "openai", "text-embedding-3-large", 8_192, 0.00013),
    ]
    audio = [
        catalog.ModelInfo("openai/gpt-4o-mini-transcribe", "openai", "GPT-4o mini Transcribe", "transcribe", 16_000, 2_000, False, False, 0.003, 0.005),
        catalog.ModelInfo("openai/gpt-4o-transcribe", "openai", "GPT-4o Transcribe", "transcribe", 16_000, 2_000, False, False, 0.006, 0.01),
    ]
    catalog.set_snapshot(chat, embeddings, audio=audio)
    yield
    catalog._current = None


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)
    yield
    Base.metadata.drop_all(test_engine)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "central_only(reason): seeds or reads rows through the application's own session factory, which "
        "(rightly) has no default client database; skipped under HUNTERAI_TENANT_TESTS=1",
    )


def pytest_collection_modifyitems(config, items):
    if not TENANT_TESTS:
        return
    for item in items:
        marker = item.get_closest_marker("central_only")
        if marker:
            item.add_marker(pytest.mark.skip(reason=marker.args[0] if marker.args else "central-only test harness"))


@pytest.fixture(autouse=True)
def tenant_mode(clean_database, monkeypatch):
    """Under HUNTERAI_TENANT_TESTS=1, every client created is born using its own database."""
    if not TENANT_TESTS:
        yield
        return
    import uuid as _uuid
    from datetime import timedelta

    from sqlalchemy import event

    from app import database
    from app.models import Client, ClientDataStore, now_utc
    from app.security import encrypt_secret
    from app.services import data_store, tenant_schema

    schema = f"hunterai_tt_{_uuid.uuid4().hex[:8]}"
    with test_engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    tenant = database.tenant_engine(os.environ["DATABASE_URL"], schema)
    tenant_schema.upgrade_engine(tenant)
    monkeypatch.setattr(data_store, "SCHEMA", schema)
    _DIRECT_TENANT.update({"client_id": None, "dsn": os.environ["DATABASE_URL"], "schema": schema})

    def born_in_own_database(session, flush_context, instances):
        for obj in list(session.new):
            if isinstance(obj, Client) and obj.data_store is None:
                obj.data_mode = "supabase"
                obj.data_store = ClientDataStore(
                    agency_id=obj.agency_id, status="connected", encrypted_dsn=encrypt_secret(os.environ["DATABASE_URL"]),
                    schema_version=tenant_schema.head(), connect_expires_at=now_utc() + timedelta(days=1),
                )
                database.use_client(session, obj)

    event.listen(RoutingSession, "before_flush", born_in_own_database)
    try:
        yield
    finally:
        event.remove(RoutingSession, "before_flush", born_in_own_database)
        _DIRECT_TENANT.clear()
        tenant.dispose()
        with test_engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def authenticated_client(client: TestClient):
    response = client.post(
        "/api/auth/register",
        json={
            "agency_name": "Agencia Prisma",
            "name": "Ana Admin",
            "email": "ana@prisma.com",
            "password": "contrasena-segura",
        },
    )
    assert response.status_code == 201
    return client


def customer_conversation(client: TestClient, agent_id: str) -> dict:
    """A conversation as a customer would open it (widget channel), so it shows
    in the client portal. Playground conversations are rehearsals and never do."""
    from app.models import Conversation

    created = client.post("/api/conversations", json={"agent_id": agent_id}).json()
    with TestingSession() as db:
        conversation = db.get(Conversation, created["id"])
        conversation.channel = "widget"
        db.commit()
    created["channel"] = "widget"
    return created


def login_legacy_owner(client: TestClient) -> dict:
    """Preserve access isolation for data created by older releases.

    Seed the existing owner directly; current setup must never create another
    agency. Login still needs to work for every existing owner after an upgrade.
    """
    from app.models import Agency, User
    from app.security import hash_password

    credentials = {"email": "legacy-owner@example.com", "password": "legacy-owner-password"}
    with TestingSession() as db:
        db.add(User(
            agency=Agency(name="Legacy agency", slug="legacy-agency"),
            name="Legacy owner",
            email=credentials["email"],
            password_hash=hash_password(credentials["password"]),
        ))
        db.commit()
    response = client.post("/api/auth/login", json=credentials)
    assert response.status_code == 200, response.text
    return response.json()
