"""The suite wipes its database between runs; it must never do that to a real one.

A run killed mid-test leaves the central schema half-built and the next
``create_all`` dies on a leftover ``pg_type`` entry, failing every test after it
at once. So each session starts from an empty database
(``conftest.reset_test_database``). That is only safe because the function
refuses a database that is not plainly a test one, and this pins that refusal:
without it a mistyped ``TEST_DATABASE_URL`` would drop a development schema.
"""

import pytest

from conftest import reset_test_database


def test_it_refuses_a_database_that_is_not_a_test_one(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://openlivery:openlivery@localhost:5432/openlivery")
    with pytest.raises(RuntimeError, match="test database"):
        reset_test_database()


def test_it_refuses_an_unnamed_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://openlivery:openlivery@localhost:5432/")
    with pytest.raises(RuntimeError, match="test database"):
        reset_test_database()


def test_it_accepts_the_test_database_it_is_pointed_at(monkeypatch):
    """The guard must not be a blanket refusal: the suite has to be able to run."""
    from sqlalchemy.engine import make_url

    import conftest

    name = make_url(conftest.os.environ["DATABASE_URL"]).database or ""
    assert "test" in name, f"the suite is pointed at {name!r}; it only ever runs on a test database"
