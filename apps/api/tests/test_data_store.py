"""Connecting a client's own Supabase project, with the Management API scripted."""

import base64
import hashlib
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.models import now_utc
from app.services import data_store, supabase_mgmt

REF = "abcdefghijklmnopqrst"
PROJECTS = [{"ref": REF, "name": "Acme prod", "region": "us-east-1", "status": "ACTIVE_HEALTHY"}]
POOLER = {"database_type": "PRIMARY", "db_host": "aws-0-us-east-1.pooler.supabase.com", "db_port": 6543, "db_name": "postgres"}


class Scripted:
    def __init__(self):
        self.verifiers: list[str] = []
        self.queries: list[str] = []
        self.dsns: list[str] = []
        self.pooler = dict(POOLER)

    async def exchange_code(self, code, verifier):
        self.verifiers.append(verifier)
        return supabase_mgmt.Grant("access-1", "refresh-1", now_utc() + timedelta(hours=1))

    async def refresh(self, token):
        return supabase_mgmt.Grant("access-2", token, now_utc() + timedelta(hours=1))

    async def list_projects(self, access):
        return PROJECTS

    async def run_query(self, access, ref, query):
        self.queries.append(query)
        return []

    async def pooler_config(self, access, ref):
        return self.pooler

    def probe(self, dsn):
        self.dsns.append(dsn)
        return 12_345_678


@pytest.fixture
def scripted(monkeypatch):
    fake = Scripted()
    for name in ("exchange_code", "refresh", "list_projects", "run_query", "pooler_config"):
        monkeypatch.setattr(supabase_mgmt, name, getattr(fake, name))
    monkeypatch.setattr(data_store, "probe_dsn", fake.probe)
    settings = get_settings()
    monkeypatch.setattr(settings, "supabase_oauth_client_id", "client-id")
    monkeypatch.setattr(settings, "supabase_oauth_client_secret", "client-secret")
    return fake


def _setup(client):
    customer = client.post("/api/clients", json={"name": "Acme", "is_active": True}).json()
    link = client.post(f"/api/clients/{customer['id']}/datastore/link").json()
    return customer, link["connect_url"].rsplit("/", 1)[1]


def _authorize(anonymous, token):
    url = anonymous.post(f"/api/datastore/connect/{token}/start").json()["authorization_url"]
    query = parse_qs(urlparse(url).query)
    assert query["code_challenge_method"] == ["S256"]
    back = anonymous.get("/api/supabase/oauth/callback", params={"state": query["state"][0], "code": "the-code"},
                         follow_redirects=False)
    return query, back


@pytest.mark.central_only("starts from a client with no database connected")
def test_the_whole_connection_on_the_public_link(authenticated_client, scripted):
    client = authenticated_client
    customer, token = _setup(client)
    anonymous = TestClient(client.app)

    assert anonymous.get(f"/api/datastore/connect/{token}").json()["status"] == "pending"
    query, back = _authorize(anonymous, token)
    assert back.status_code in (302, 307)
    assert back.headers["location"].endswith(f"/connect/supabase/{token}?result=authorized")
    # PKCE: the verifier sent to the token endpoint matches the challenge sent to Supabase.
    digest = base64.urlsafe_b64encode(hashlib.sha256(scripted.verifiers[0].encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == [digest]
    # A consent trip is single use.
    again = anonymous.get("/api/supabase/oauth/callback", params={"state": query["state"][0], "code": "x"}, follow_redirects=False)
    assert "result=expired" in again.headers["location"]

    assert anonymous.get(f"/api/datastore/connect/{token}/projects").json() == PROJECTS
    chosen = anonymous.post(f"/api/datastore/connect/{token}/project", json={"ref": REF})
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["status"] == "connected" and chosen.json()["project_name"] == "Acme prod"

    # OpenLivery created its own role; the project's own password was never needed.
    assert "CREATE ROLE hunterai_app LOGIN PASSWORD" in scripted.queries[0]
    # A schema already used by someone else is refused, never adopted.
    assert "RAISE EXCEPTION" in scripted.queries[0] and "c.relowner <>" in scripted.queries[0]
    assert scripted.dsns[0].startswith(f"postgresql://hunterai_app.{REF}:")
    assert "@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require" in scripted.dsns[0]
    password = scripted.dsns[0].split(":")[2].split("@")[0]

    state = client.get(f"/api/clients/{customer['id']}/datastore").json()
    assert state["status"] == "connected" and state["db_size_bytes"] == 12_345_678
    for body in (state, chosen.json()):
        assert password not in str(body) and "refresh-1" not in str(body)

    disconnected = client.delete(f"/api/clients/{customer['id']}/datastore").json()
    assert disconnected["status"] == "pending" and disconnected["project_ref"] == ""


def test_a_denied_consent_and_an_unknown_link(authenticated_client, scripted):
    client = authenticated_client
    _, token = _setup(client)
    anonymous = TestClient(client.app)
    url = anonymous.post(f"/api/datastore/connect/{token}/start").json()["authorization_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    denied = anonymous.get("/api/supabase/oauth/callback", params={"state": state, "error": "access_denied"}, follow_redirects=False)
    assert denied.headers["location"].endswith("result=denied")
    assert anonymous.get("/api/datastore/connect/not-a-token").status_code == 404
    assert anonymous.post(f"/api/datastore/connect/{token}/project", json={"ref": REF}).status_code == 409


def test_a_database_host_outside_supabase_is_refused(authenticated_client, scripted):
    client = authenticated_client
    _, token = _setup(client)
    anonymous = TestClient(client.app)
    _authorize(anonymous, token)
    scripted.pooler["db_host"] = "169.254.169.254"
    refused = anonymous.post(f"/api/datastore/connect/{token}/project", json={"ref": REF})
    assert refused.status_code == 502 and scripted.dsns == []
    # A project the grant cannot see is refused before anything runs on it.
    other = anonymous.post(f"/api/datastore/connect/{token}/project", json={"ref": "zzzzzzzzzzzzzzzzzzzz"})
    assert other.status_code == 404


def test_without_the_oauth_app_the_link_says_so(authenticated_client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "supabase_oauth_client_id", "")
    monkeypatch.setattr(settings, "supabase_oauth_client_secret", "")
    client = authenticated_client
    _, token = _setup(client)
    anonymous = TestClient(client.app)
    assert anonymous.get(f"/api/datastore/connect/{token}").json()["oauth_ready"] is False
    assert anonymous.post(f"/api/datastore/connect/{token}/start").status_code == 503


def test_renewing_the_link_voids_consents_in_flight(authenticated_client, scripted):
    client = authenticated_client
    customer, token = _setup(client)
    anonymous = TestClient(client.app)
    url = anonymous.post(f"/api/datastore/connect/{token}/start").json()["authorization_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    client.post(f"/api/clients/{customer['id']}/datastore/link")
    late = anonymous.get("/api/supabase/oauth/callback", params={"state": state, "code": "c"}, follow_redirects=False)
    assert "result=expired" in late.headers["location"]
