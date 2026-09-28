"""The platform migrations upgrade an installation that already has data:
every existing row survives, and the new columns are filled with the
defaults the previous release's behaviour equals (active, everything on).

The suite builds its tables with ``create_all`` at the head, so this test
first rewinds the schema to what ``0074`` left — no platform tables, no
``agencies`` access or feature columns — then runs the three platform
migrations the way an upgrade does, and checks the data.
"""

import importlib.util
import pathlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text

from app.models import Agency, Client, PlatformAdmin, User, UsageRecord
from conftest import TestingSession, test_engine

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _run_migration(name: str, function_name: str) -> None:
    spec = importlib.util.spec_from_file_location(name, MIGRATIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with test_engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            getattr(module, function_name)()


def _rewind() -> None:
    with test_engine.begin() as connection:
        for table in ("agency_slug_aliases", "platform_audit_events", "agency_admin_invitations", "platform_admins"):
            connection.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        for column in ("features", "plan", "updated_at", "access_block_reason", "access_blocked_at", "access_status"):
            connection.execute(text(f"ALTER TABLE agencies DROP COLUMN IF EXISTS {column}"))


@pytest.mark.central_only("exercises central migrations on the central schema")
def test_the_platform_migrations_preserve_existing_data_and_fill_the_defaults():
    from app.security import hash_password

    with TestingSession() as db:
        agency = Agency(name="Existing agency", slug="existing-agency")
        db.add(agency)
        db.flush()
        db.add(User(agency=agency, name="Owner", email="owner@existing.example.com",
                    password_hash=hash_password("password")))
        db.add(Client(agency_id=agency.id, name="Client", portal_slug="existing-client"))
        db.add(UsageRecord(agency_id=agency.id, provider="openrouter", model="openai/gpt-5.6-luna",
                           input_tokens=7, output_tokens=3))
        db.commit()
        agency_id, user_id = agency.id, None
        user_id = db.scalar(select(User.id).where(User.email == "owner@existing.example.com"))

    _rewind()
    _run_migration("0075_platform_accounts", "upgrade")
    _run_migration("0076_agency_access", "upgrade")
    _run_migration("0077_agency_features", "upgrade")

    with TestingSession() as db:
        agency = db.get(Agency, agency_id)
        assert agency is not None
        assert agency.access_status == "active"
        assert agency.access_blocked_at is None
        assert agency.access_block_reason == ""
        assert agency.plan == ""
        assert all(value is True for value in agency.features.values())
        assert db.get(User, user_id) is not None
        assert db.scalar(select(Client.id).where(Client.portal_slug == "existing-client")) is not None
        assert db.scalar(select(UsageRecord.id).where(UsageRecord.agency_id == agency_id)) is not None
        # The new tables exist and hold nothing yet.
        assert db.scalar(select(PlatformAdmin.id).limit(1)) is None
