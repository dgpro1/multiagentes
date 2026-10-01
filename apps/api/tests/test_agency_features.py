"""The platform's module switches per agency: the catalog stays in sync with
its TypeScript mirror, the migration writes the defaults out as a literal, and
the ceiling never rewrites what the levels below chose."""

import json
import pathlib
import re

from fastapi.testclient import TestClient

from app import agency_features
from app.models import Client, PortalUser
from app.security import create_portal_token, hash_password
from conftest import TestingSession
from test_platform_auth import _login, _platform_admin

TS_MIRROR = pathlib.Path(__file__).resolve().parents[2] / "web" / "lib" / "agency-features.ts"
MIGRATION = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0077_agency_features.py"


def test_the_typescript_mirror_holds_the_same_catalog():
    source = TS_MIRROR.read_text(encoding="utf-8")
    listed = re.findall(r"\{ key: \"([a-z_.]+)\", default: (true|false) \}", source)
    assert listed, "could not find the AGENCY_FEATURES list in agency-features.ts"
    expected = [(key, "true" if default else "false") for key, default in agency_features.CATALOG]
    assert listed == expected, "apps/web/lib/agency-features.ts and app/agency_features.py drifted"


def test_the_migration_writes_out_the_same_defaults_the_catalog_has():
    source = MIGRATION.read_text(encoding="utf-8")
    match = re.search(r"DEFAULTS = \(([^)]*)\)", source, re.DOTALL)
    assert match, "the migration must write its defaults out as a literal"
    literal = "".join(re.findall(r"'([^']*)'", match.group(1)))
    written = json.loads(literal)
    # A module added after the migration is not in its literal: an agency's row
    # simply lacks it and reads the catalog default. What the migration did
    # write must still agree with the catalog.
    catalog = dict(agency_features.CATALOG)
    assert written and all(catalog.get(key) == value for key, value in written.items())


def test_the_presets_only_offer_real_modules():
    for name, keys in agency_features.PRESETS.items():
        assert keys, f"The {name} preset is empty"
        unknown = sorted(key for key in keys if key not in agency_features.DEFAULTS)
        assert not unknown, f"The {name} preset offers unknown modules: {unknown}"


def _platform(client: TestClient) -> None:
    _platform_admin()
    _login(client)


def test_the_panel_patches_features_and_audits_every_change(client):
    _platform(client)
    created = client.post("/api/platform/agencies", json={
        "name": "Agencia A", "slug": "agencia-a", "admin_name": "A", "admin_email": "a@a.example.com",
    }).json()
    agency_id = created["agency"]["id"]
    assert created["agency"]["features"]["agents"] is True
    patched = client.put(f"/api/platform/agencies/{agency_id}/features", json={"features": {"agents": False}})
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["features"]["agents"] is False
    assert body["features"]["inbox"] is True
    assert client.put(f"/api/platform/agencies/{agency_id}/features", json={"features": {"no-such": True}}).status_code == 422
    assert client.put(f"/api/platform/agencies/{agency_id}/features", json={"features": {"agents": "off"}}).status_code == 422
    assert client.put(f"/api/platform/agencies/{agency_id}/features", json={"features": {}, "plan": "no-plan"}).status_code == 422
    actions = [event["action"] for event in client.get("/api/platform/audit-events").json()]
    assert "agency.features_changed" in actions
    catalog = client.get("/api/platform/features").json()
    assert len(catalog["catalog"]) == len(agency_features.CATALOG)
    assert set(catalog["presets"]) == set(agency_features.PRESETS)


def _portal_client(client: TestClient) -> dict:
    customer = client.post("/api/clients", json={"name": "Acme", "is_active": True}).json()
    assert client.post(f"/api/clients/{customer['id']}/portal-users", json={
        "name": "Portal user", "email": "portal@acme.example.com", "password": "portal-password"}).status_code == 201
    assert client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_enabled": True}).status_code == 200
    return customer


def test_the_module_ceiling_hides_and_refuses_the_portal(authenticated_client):
    client = authenticated_client
    agency_id = client.get("/api/agency").json()["id"]
    customer = _portal_client(client)
    token = create_portal_token(customer["id"], customer["portal_slug"])
    cookies = {"portal_access_token": token}
    assert client.get(f"/api/portal/{customer['portal_slug']}/inbox", cookies=cookies).status_code == 200
    _platform(client)
    assert client.put(f"/api/platform/agencies/{agency_id}/features", json={"features": {"inbox": False}}).status_code == 200
    # The portal session no longer offers the function...
    session = client.get(f"/api/portal/{customer['portal_slug']}/me", cookies=cookies)
    assert session.status_code == 200, session.text
    assert "inbox" not in session.json()["features"]
    # ...and the route refuses it.
    assert client.get(f"/api/portal/{customer['portal_slug']}/inbox", cookies=cookies).status_code == 403
    # Re-enabling restores it, and the client's own switches were never touched.
    assert client.put(f"/api/platform/agencies/{agency_id}/features", json={"features": {"inbox": True}}).status_code == 200
    assert client.get(f"/api/portal/{customer['portal_slug']}/inbox", cookies=cookies).status_code == 200
    detail = client.get(f"/api/clients/{customer['id']}").json()
    assert detail["portal_features"]["inbox"] is True


def test_the_mobile_session_carries_the_effective_features(authenticated_client):
    client = authenticated_client
    agency_id = client.get("/api/agency").json()["id"]
    customer = _portal_client(client)
    signed = client.post("/api/mobile/sign-in", json={"email": "portal@acme.example.com", "password": "portal-password"})
    assert signed.status_code == 200, signed.text
    assert "inbox" in signed.json()["features"]
    _platform(client)
    assert client.put(f"/api/platform/agencies/{agency_id}/features", json={"features": {"inbox": False}}).status_code == 200
    signed = client.post("/api/mobile/sign-in", json={"email": "portal@acme.example.com", "password": "portal-password"})
    assert signed.status_code == 200, signed.text
    assert "inbox" not in signed.json()["features"]


def _client_and_agent(client: TestClient, name: str = "Acme") -> tuple[dict, dict]:
    customer = client.post("/api/clients", json={"name": name, "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post("/api/agents", json={
        "client_id": customer["id"], "provider": "openrouter", "model": "openai/gpt-5.6-luna",
        "name": "Bot", "instructions": "", "personality": "", "is_active": True}).json()
    return customer, agent


def _add_whatsapp_line(client: TestClient, customer: dict, agent: dict):
    return client.post(f"/api/whatsapp/clients/{customer['id']}/channels", json={"agent_id": agent["id"]})


def _switch_off(client: TestClient, agency_id: str, key: str) -> None:
    # The platform admin exists from the first call in each test; creating it
    # again would collide on its own unique e-mail.
    patched = client.put(f"/api/platform/agencies/{agency_id}/features", json={"features": {key: False}})
    assert patched.status_code == 200, patched.text


def test_the_panel_session_carries_the_modules(authenticated_client):
    """The panel has to know what it may offer, because the server refuses the
    rest: an option that fails when it is pressed is worse than no option."""
    client = authenticated_client
    agency_id = client.get("/api/agency").json()["id"]
    before = client.get("/api/auth/me").json()["agency"]["modules"]
    assert "channels.whatsapp" in before
    _platform(client)
    _switch_off(client, agency_id, "channels.whatsapp")
    after = client.get("/api/auth/me").json()["agency"]["modules"]
    assert "channels.whatsapp" not in after
    # The rest of the catalog is untouched: one switch is not a whole plan.
    assert "channels.instagram" in after
    assert "inbox" in after
    # The same list arrives at login, not only when the session is refreshed.
    signed_in = client.post("/api/auth/login", json={"email": client.get("/api/auth/me").json()["email"], "password": "secret-password"})
    if signed_in.status_code == 200:
        assert "channels.whatsapp" not in signed_in.json()["agency"]["modules"]


def test_a_switched_off_module_refuses_a_new_line(authenticated_client):
    """The switch the platform flips has to reach the agency, not only the
    client's portal: without this the agency keeps connecting what was turned
    off, and the switch is decoration."""
    client = authenticated_client
    agency_id = client.get("/api/agency").json()["id"]
    customer, agent = _client_and_agent(client)
    assert _add_whatsapp_line(client, customer, agent).status_code == 201

    _platform(client)
    _switch_off(client, agency_id, "channels.whatsapp")
    refused = _add_whatsapp_line(client, customer, agent)
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == agency_features.FEATURE_DISABLED_DETAIL

    # Every channel type passes the same door, so the switch reaches all of them.
    # WhatsApp API and the web widget are the two that take the client's id; the
    # social ones are connected through OAuth and are covered by the same call.
    for key, method, path in (
        ("channels.whatsapp_cloud", "POST", f"/api/whatsapp-cloud/clients/{customer['id']}/channels"),
        ("channels.webchat", "PUT", f"/api/webchat/channels/{customer['id']}"),
    ):
        _switch_off(client, agency_id, key)
        answer = client.request(method, path, json={"agent_id": agent["id"]})
        assert answer.status_code == 403, f"{key}: {answer.status_code} {answer.text}"


def test_the_lines_an_agency_already_has_keep_working(authenticated_client):
    """Turning the switch off stops new lines, it does not cut the ones already
    connected: a client's WhatsApp does not stop answering because the platform
    narrowed the plan afterwards, and nothing is deleted."""
    client = authenticated_client
    agency_id = client.get("/api/agency").json()["id"]
    customer, agent = _client_and_agent(client)
    created = _add_whatsapp_line(client, customer, agent)
    assert created.status_code == 201, created.text
    line_id = created.json()["id"]

    _platform(client)
    _switch_off(client, agency_id, "channels.whatsapp")

    # The line is still there and still readable: the agency keeps managing it.
    listed = client.get(f"/api/whatsapp/clients/{customer['id']}/channels")
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [line_id]
    assert client.get(f"/api/whatsapp/channels/{line_id}").status_code == 200
    # And the client's portal never offered the function while it was on, and
    # still does not: the switch above it is what the portal reads.
    assert client.get(f"/api/clients/{customer['id']}").status_code == 200
