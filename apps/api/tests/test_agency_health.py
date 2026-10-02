"""The periodic check of an agency's own project and bucket: a disconnection shows on its screen."""

import asyncio

from app.services import agency_backend, supabase_mgmt as supabase
from conftest import TestingSession
from test_agency_backend import _connect_project, _module, agency_project  # noqa: F401 - agency_project is a fixture
from test_agency_storage import AGENCY, buckets  # noqa: F401 - buckets is a fixture


def _sweep() -> int:
    async def run() -> int:
        with TestingSession() as db:
            return await agency_backend.check_all(db)

    return asyncio.run(run())


def test_a_project_and_a_bucket_that_stop_answering_are_flagged_and_recover(authenticated_client, agency_project, buckets, monkeypatch):  # noqa: F811
    client = authenticated_client
    _module(client, True)
    _connect_project(client)
    assert client.put("/api/agency/storage", json=AGENCY).status_code == 200
    assert _sweep() == 0

    answer = supabase.run_query

    async def paused(access, ref, query):
        raise supabase.SupabaseError("paused")

    monkeypatch.setattr(supabase, "run_query", paused)
    buckets[AGENCY["access_key_id"]].refuse = True
    assert _sweep() == 2
    project, bucket = client.get("/api/agency/backend").json(), client.get("/api/agency/storage").json()
    assert (project["status"], bucket["status"]) == ("error", "error")
    assert project["last_error"] and bucket["last_error"]

    monkeypatch.setattr(supabase, "run_query", answer)
    buckets[AGENCY["access_key_id"]].refuse = False
    assert _sweep() == 0
    assert client.get("/api/agency/backend").json()["status"] == "connected"
    assert client.get("/api/agency/storage").json()["status"] == "connected"


def test_an_agency_without_the_module_is_left_alone(authenticated_client, agency_project, buckets, monkeypatch):  # noqa: F811
    client = authenticated_client
    _module(client, True)
    _connect_project(client)
    _module(client, False)

    async def boom(access, ref, query):
        raise AssertionError("an agency without the module must not be probed")

    monkeypatch.setattr(supabase, "run_query", boom)
    assert _sweep() == 0
