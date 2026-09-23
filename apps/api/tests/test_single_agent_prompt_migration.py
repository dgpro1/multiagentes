"""0057 clears the retired prompt fields and leaves each agent's instructions alone.

The suite builds its tables with ``create_all`` and never runs a migration, so
this loads the migration module and runs its ``upgrade()`` through Alembic's
``Operations`` against the test database.
"""

import importlib.util
import pathlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.models import Agency, Agent, Client
from tests.conftest import TestingSession, test_engine

MIGRATION = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0057_single_agent_prompt.py"
RETIRED = ("personality", "brief_summary", "brief_products", "brief_audience", "brief_policies", "brief_dos", "brief_donts")


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0057", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(function_name: str) -> None:
    module = _load_migration()
    with test_engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            getattr(module, function_name)()


def _agent(db, client, name: str, **fields) -> Agent:
    agent = Agent(agency_id=client.agency_id, client_id=client.id, name=name, **fields)
    db.add(agent)
    db.flush()
    return agent


def test_upgrade_blanks_the_retired_fields_and_keeps_instructions():
    with TestingSession() as db:
        agency = Agency(name="Agency", slug="agency")
        db.add(agency)
        db.flush()
        client = Client(agency_id=agency.id, name="Client", portal_slug="client")
        db.add(client)
        db.flush()
        filled = _agent(
            db,
            client,
            "Filled",
            instructions="Answer bookings politely.",
            personality="Warm and brief",
            brief_summary="A dental clinic",
            brief_products="Cleanings, whitening",
            brief_audience="Families",
            brief_policies="24h cancellation",
            brief_dos="Confirm the date",
            brief_donts="Quote prices over chat",
        )
        bare = _agent(db, client, "Bare", instructions="Only instructions.")
        db.commit()
        filled_id, bare_id = filled.id, bare.id

    _run("upgrade")

    with TestingSession() as db:
        filled = db.get(Agent, filled_id)
        bare = db.get(Agent, bare_id)
        for column in RETIRED:
            assert getattr(filled, column) == "", column
            assert getattr(bare, column) == "", column
        assert filled.instructions == "Answer bookings politely."
        assert bare.instructions == "Only instructions."
        assert db.execute(text("SELECT count(*) FROM agents")).scalar() == 2


def test_downgrade_is_a_harmless_no_op():
    with TestingSession() as db:
        agency = Agency(name="Agency", slug="agency")
        db.add(agency)
        db.flush()
        client = Client(agency_id=agency.id, name="Client", portal_slug="client")
        db.add(client)
        db.flush()
        agent = _agent(db, client, "Agent", instructions="Keep me", brief_summary="kept as is")
        db.commit()
        agent_id = agent.id

    _run("downgrade")

    with TestingSession() as db:
        agent = db.get(Agent, agent_id)
        assert agent.instructions == "Keep me"
        assert agent.brief_summary == "kept as is"
