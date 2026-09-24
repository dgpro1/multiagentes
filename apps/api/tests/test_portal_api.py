"""API integrations managed from the client portal.

With the ``api`` portal function on, a client's portal admins get the agency's
API tab for their own client under ``/api/portal/{slug}/manage/integrations``:
the same handlers, confined to that client and narrower where credentials are
concerned. These tests pin the door (function, role, session), the confinement
(another client's and agency-wide integrations are 404 everywhere), what the
portal may grant (scope whitelist, forced client), the caps, the webhook
destination guard, and that the panel and the public API do not change.
"""

import asyncio
import re
import socket
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.deps import get_current_user
from app.models import ApiIntegration, ApiToken, WebhookDelivery, WebhookSubscription
from app.portal_api_access import MAX_INTEGRATIONS, MAX_TOKENS, MAX_WEBHOOKS
from app.portal_features import FEATURE_DISABLED_DETAIL
from app.portal_permissions import API_MANAGE, has_permission, permissions_for
from app.routers import portal_manage
from app.services import outbound_webhooks
from app.services.api_credentials import digest
from conftest import TestingSession

PASSWORD = "secure-portal"
FORBIDDEN = {"detail": "Your role cannot do this"}
GOOD_URL = "https://hooks.example.com/openlivery"

EXPECTED_ROUTES = {
    ("GET", "/integrations/scopes"),
    ("GET", "/integrations"),
    ("POST", "/integrations"),
    ("PATCH", "/integrations/{integration_id}"),
    ("DELETE", "/integrations/{integration_id}"),
    ("GET", "/integrations/{integration_id}/tokens"),
    ("POST", "/integrations/{integration_id}/tokens"),
    ("DELETE", "/integrations/{integration_id}/tokens/{token_id}"),
    ("GET", "/integrations/{integration_id}/webhooks"),
    ("POST", "/integrations/{integration_id}/webhooks"),
    ("DELETE", "/integrations/{integration_id}/webhooks/{subscription_id}"),
    ("GET", "/integrations/{integration_id}/webhooks/{subscription_id}/deliveries"),
    ("POST", "/integrations/{integration_id}/webhooks/{subscription_id}/deliveries/{delivery_id}/replay"),
}
BODIES = {
    ("POST", "/integrations"): {"name": "Zapier"},
    ("PATCH", "/integrations/{integration_id}"): {"name": "Renamed"},
    ("POST", "/integrations/{integration_id}/webhooks"): {"url": GOOD_URL, "events": ["message.received"]},
}


def _fill(path: str, **known: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: known.get(m.group(1)) or str(uuid.uuid4()), path)


def _sign_in(client: TestClient, customer: dict, email: str) -> None:
    response = client.post(f"/api/portal/{customer['portal_slug']}/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text


def _person(client: TestClient, customer: dict, email: str, role: str) -> None:
    created = client.post(
        f"/api/clients/{customer['id']}/portal-users",
        json={"name": email.split("@")[0].title(), "email": email, "password": PASSWORD, "role": role},
    )
    assert created.status_code == 201, created.text


def _switch(client: TestClient, customer: dict, features: dict) -> None:
    response = client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_features": features})
    assert response.status_code == 200, response.text


@pytest.fixture(autouse=True)
def resolver(monkeypatch):
    """Names resolve to a public address, except the ones a test marks internal."""
    real = socket.getaddrinfo

    def fake(host, *args, **kwargs):
        if host == "hooks.example.com" or host.endswith(".public.test"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        if host.endswith(".internal.test"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.fixture
def world(authenticated_client: TestClient) -> dict:
    """Two clients of one agency; the first has the API function on and an admin
    signed in to its portal (the agency stays signed in on the same test client)."""
    agency = authenticated_client
    mine = agency.post("/api/clients", json={"name": "Mine Co", "is_active": True}).json()
    other = agency.post("/api/clients", json={"name": "Other Co", "is_active": True}).json()
    _person(agency, mine, "boss@mine.co", "admin")
    _person(agency, mine, "helper@mine.co", "agent")
    assert agency.patch(f"/api/clients/{mine['id']}/portal", json={"portal_enabled": True}).status_code == 200
    _switch(agency, mine, {"api": True})
    _sign_in(agency, mine, "boss@mine.co")
    return {"api": agency, "mine": mine, "other": other, "base": f"/api/portal/{mine['portal_slug']}/manage"}


def _agency_integration(world: dict, **payload) -> dict:
    response = world["api"].post("/api/integrations", json={"name": "Agency made", "preset": "read_only", **payload})
    assert response.status_code == 201, response.text
    return response.json()


def _make(world: dict, name: str = "Zapier", **payload) -> dict:
    response = world["api"].post(f"{world['base']}/integrations", json={"name": name, "scopes": ["inbox.read"], **payload})
    assert response.status_code == 201, response.text
    return response.json()


def _issue(world: dict, integration_id: str, days: int = 30) -> dict:
    response = world["api"].post(f"{world['base']}/integrations/{integration_id}/tokens", json={"expires_in_days": days})
    assert response.status_code == 201, response.text
    return response.json()


# The door -------------------------------------------------------------------


def test_only_admins_hold_the_permission():
    assert has_permission("admin", API_MANAGE) and not has_permission("agent", API_MANAGE)
    assert API_MANAGE in permissions_for("admin") and API_MANAGE not in permissions_for("agent")


def test_the_mounted_surface_is_exactly_the_api_tab_and_none_takes_a_panel_user():
    mounted = set()
    for route in portal_manage.router.routes:
        path = route.path.removeprefix("/portal/{slug}/manage")
        if not path.startswith("/integrations"):
            continue
        mounted |= {(method, path) for method in route.methods - {"HEAD", "OPTIONS"}}
        seen, stack = [], [route.dependant]
        while stack:
            node = stack.pop()
            seen.append(node.call)
            stack.extend(node.dependencies)
        assert portal_manage.ACTORS & set(seen), route.path
        assert get_current_user not in seen, route.path
    assert mounted == EXPECTED_ROUTES


def test_the_oauth_client_registration_stays_with_the_agency(world):
    integration = _make(world)
    path = f"{world['base']}/integrations/{integration['id']}/oauth-client"
    assert world["api"].post(path, json={"redirect_uris": ["https://a.example.com/cb"]}).status_code in (404, 405)
    assert world["api"].post(f"{world['base']}/oauth/authorize").status_code in (404, 405)


def test_without_a_session_every_mounted_route_is_a_401(world):
    world["api"].cookies.delete("portal_access_token")
    for method, path in sorted(EXPECTED_ROUTES):
        response = world["api"].request(method, world["base"] + _fill(path), json=BODIES.get((method, path), {}))
        assert response.status_code == 401, f"{method} {path}: {response.status_code}"


def test_the_agent_role_is_refused_everywhere(world):
    _sign_in(world["api"], world["mine"], "helper@mine.co")
    for method, path in sorted(EXPECTED_ROUTES):
        response = world["api"].request(method, world["base"] + _fill(path), json=BODIES.get((method, path), {}))
        assert response.status_code == 403, f"{method} {path}: {response.status_code}"
        assert response.json() == FORBIDDEN, f"{method} {path}"


def test_with_the_function_off_every_mounted_route_is_refused_and_nothing_is_touched(world):
    integration = _make(world)
    _switch(world["api"], world["mine"], {"api": False})
    for method, path in sorted(EXPECTED_ROUTES):
        response = world["api"].request(
            method, world["base"] + _fill(path, integration_id=integration["id"]), json=BODIES.get((method, path), {}),
        )
        assert response.status_code == 403, f"{method} {path}: {response.status_code}"
        assert response.json() == {"detail": FEATURE_DISABLED_DETAIL}, f"{method} {path}"
    # The integration itself is untouched, and the agency still sees it.
    listed = world["api"].get("/api/integrations").json()
    assert [row["name"] for row in listed] == ["Zapier"]


# The admin's flow -----------------------------------------------------------


def test_an_admin_runs_the_whole_screen_and_the_secrets_are_shown_once(world):
    api, base, mine = world["api"], world["base"], world["mine"]
    integration = _make(world, scopes=["inbox.read", "contacts.read"])
    assert integration["client_id"] == mine["id"] and integration["client_name"] == "Mine Co"
    assert integration["scopes"] == ["contacts.read", "inbox.read"]

    renamed = api.patch(f"{base}/integrations/{integration['id']}", json={"name": "Zapier prod"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Zapier prod"
    assert [row["name"] for row in api.get(f"{base}/integrations").json()] == ["Zapier prod"]

    issued = _issue(world, integration["id"])
    assert issued["token"].startswith("ol_")
    with TestingSession() as db:
        stored = db.scalars(select(ApiToken).where(ApiToken.integration_id == uuid.UUID(integration["id"]))).all()
    assert [row.token_hash for row in stored] == [digest(issued["token"])]
    assert issued["token"] not in stored[0].token_hash and stored[0].token_prefix == issued["token_prefix"]
    listed = api.get(f"{base}/integrations/{integration['id']}/tokens")
    assert listed.status_code == 200 and issued["token"] not in listed.text
    assert issued["token"] not in api.get(f"{base}/integrations").text

    assert api.delete(f"{base}/integrations/{integration['id']}/tokens/{stored[0].id}").status_code == 204
    assert api.get(f"{base}/integrations/{integration['id']}/tokens").json() == []
    stale = api.get("/api/v1/clients", headers={"Authorization": f"Bearer {issued['token']}"})
    assert stale.status_code == 401

    hook = api.post(f"{base}/integrations/{integration['id']}/webhooks", json={"url": GOOD_URL, "events": ["message.received"]})
    assert hook.status_code == 201 and hook.json()["secret"].startswith("whsec_")
    subscription_id = hook.json()["subscription_id"]
    subscriptions = api.get(f"{base}/integrations/{integration['id']}/webhooks")
    assert [row["id"] for row in subscriptions.json()] == [subscription_id] and hook.json()["secret"] not in subscriptions.text
    with TestingSession() as db:
        row = db.get(WebhookSubscription, uuid.UUID(subscription_id))
        assert hook.json()["secret"] not in row.encrypted_secret
        db.add(WebhookDelivery(subscription_id=row.id, event="message.received", payload={"client_id": mine["id"]}, status="failed"))
        db.commit()
    deliveries = api.get(f"{base}/integrations/{integration['id']}/webhooks/{subscription_id}/deliveries")
    assert deliveries.status_code == 200 and len(deliveries.json()) == 1
    replay = api.post(f"{base}/integrations/{integration['id']}/webhooks/{subscription_id}/deliveries/{deliveries.json()[0]['id']}/replay")
    assert replay.status_code == 200 and replay.json()["status"] == "pending"
    assert api.delete(f"{base}/integrations/{integration['id']}/webhooks/{subscription_id}").status_code == 204

    assert api.delete(f"{base}/integrations/{integration['id']}").status_code == 204
    assert api.get(f"{base}/integrations").json() == []


def test_who_made_it_is_recorded_and_the_tokens_act_as_an_agency_user(world):
    integration = _make(world)
    with TestingSession() as db:
        row = db.get(ApiIntegration, uuid.UUID(integration["id"]))
        assert row.created_via_portal is True
        assert row.created_by_portal_label == "Boss <boss@mine.co>"
        assert row.created_by_portal_user_id is not None
        from app.models import User
        assert db.get(User, row.created_by).email == "ana@prisma.com"
    agency_made = _agency_integration(world)
    with TestingSession() as db:
        assert db.get(ApiIntegration, uuid.UUID(agency_made["id"])).created_via_portal is False


# Confinement ----------------------------------------------------------------


def test_another_clients_and_agency_wide_integrations_are_404_on_every_route(world):
    api, base = world["api"], world["base"]
    foreign = _agency_integration(world, client_id=world["other"]["id"])
    wide = _agency_integration(world, name="Whole agency")
    with TestingSession() as db:
        hooks = {}
        for name, row in (("foreign", foreign), ("wide", wide)):
            subscription = WebhookSubscription(
                integration_id=uuid.UUID(row["id"]), url=GOOD_URL, encrypted_secret="x", events=["message.received"],
            )
            db.add(subscription)
            db.flush()
            hooks[name] = str(subscription.id)
        db.commit()
    # The agency sees all three kinds; the portal sees none of them.
    assert api.get(f"{base}/integrations").json() == []
    for name, row in (("foreign", foreign), ("wide", wide)):
        prefix = f"{base}/integrations/{row['id']}"
        for method, path, body in (
            ("PATCH", "", {"name": "Hijacked"}),
            ("DELETE", "", None),
            ("GET", "/tokens", None),
            ("POST", "/tokens", {"expires_in_days": 5}),
            ("DELETE", f"/tokens/{uuid.uuid4()}", None),
            ("GET", "/webhooks", None),
            ("POST", "/webhooks", {"url": GOOD_URL, "events": ["message.received"]}),
            ("DELETE", f"/webhooks/{hooks[name]}", None),
            ("GET", f"/webhooks/{hooks[name]}/deliveries", None),
            ("POST", f"/webhooks/{hooks[name]}/deliveries/{uuid.uuid4()}/replay", None),
        ):
            response = api.request(method, prefix + path, json=body)
            assert response.status_code == 404, f"{name} {method} {path}: {response.status_code}"
    still = {row["id"]: row for row in api.get("/api/integrations").json()}
    assert still[foreign["id"]]["name"] == "Agency made" and still[wide["id"]]["name"] == "Whole agency"
    with TestingSession() as db:
        assert db.query(ApiToken).count() == 0


def test_a_forged_client_id_never_lands_an_integration_elsewhere(world):
    api, base = world["api"], world["base"]
    forged = api.post(f"{base}/integrations", json={"name": "Sneaky", "scopes": ["inbox.read"], "client_id": world["other"]["id"]})
    assert forged.status_code == 404
    unknown = api.post(f"{base}/integrations", json={"name": "Sneaky", "scopes": ["inbox.read"], "client_id": str(uuid.uuid4())})
    assert unknown.status_code == 404
    # No client at all is not an agency-wide integration: it is forced to the portal's client.
    for body in ({}, {"client_id": None}, {"client_id": world["mine"]["id"]}):
        made = api.post(f"{base}/integrations", json={"name": "Mine", "scopes": ["inbox.read"], **body})
        assert made.status_code == 201 and made.json()["client_id"] == world["mine"]["id"], body
    # An edit cannot move it either.
    moved = api.patch(f"{base}/integrations/{made.json()['id']}", json={"client_id": world["other"]["id"], "name": "Still mine"})
    assert moved.status_code == 200 and moved.json()["client_id"] == world["mine"]["id"]
    assert all(row["client_id"] == world["mine"]["id"] for row in api.get("/api/integrations").json())


# What the portal may grant --------------------------------------------------


def test_the_catalogue_offers_only_what_the_portal_may_grant(world):
    catalogue = world["api"].get(f"{world['base']}/integrations/scopes")
    assert catalogue.status_code == 200
    keys = {row["key"] for row in catalogue.json()["scopes"]}
    assert {"inbox.read", "inbox.reply", "inbox.manage", "contacts.read", "contacts.manage", "tags.read",
            "pipeline.read", "pipeline.manage", "calendar.read", "reports.read"} <= keys
    for forbidden in ("clients.read", "clients.write", "integrations.manage", "webhooks.manage", "channels.manage",
                      "channels.read", "agents.read", "agents.write", "agents.knowledge", "agents.tools"):
        assert forbidden not in keys, forbidden
    presets = catalogue.json()["presets"]
    assert "full" not in presets and set(presets) == {"read_only", "operator"}
    assert all(set(values) <= keys for values in presets.values())
    # The agency's own catalogue is whole.
    whole = world["api"].get("/api/integrations/scopes").json()
    assert "full" in whole["presets"] and "clients.write" in {row["key"] for row in whole["scopes"]}


def test_the_catalogue_follows_the_functions_switched_on(world):
    api, base = world["api"], world["base"]
    _switch(api, world["mine"], {"contacts": False, "agents": True})
    keys = {row["key"] for row in api.get(f"{base}/integrations/scopes").json()["scopes"]}
    assert "contacts.read" not in keys and "contacts.manage" not in keys
    assert "agents.read" in keys and "agents.write" not in keys and "agents.tools" not in keys
    denied = api.post(f"{base}/integrations", json={"name": "Contacts", "scopes": ["contacts.read"]})
    assert denied.status_code == 422 and "contacts.read" in denied.json()["detail"]
    _switch(api, world["mine"], {"channels.webchat": True})
    assert "channels.read" in {row["key"] for row in api.get(f"{base}/integrations/scopes").json()["scopes"]}


@pytest.mark.parametrize("scope", [
    "clients.write", "clients.read", "integrations.manage", "webhooks.manage", "channels.manage",
    "agents.write", "agents.knowledge", "agents.tools", "agents.read", "channels.read",
])
def test_a_scope_beyond_the_whitelist_is_a_422_on_create_and_edit(world, scope):
    api, base = world["api"], world["base"]
    refused = api.post(f"{base}/integrations", json={"name": "Too much", "scopes": ["inbox.read", scope]})
    assert refused.status_code == 422 and scope in refused.json()["detail"]
    integration = _make(world)
    edit = api.patch(f"{base}/integrations/{integration['id']}", json={"scopes": [scope]})
    assert edit.status_code == 422
    assert api.get(f"{base}/integrations").json()[0]["scopes"] == ["inbox.read"]


def test_the_full_preset_is_refused_and_other_presets_are_narrowed_and_unknown_keys_still_400(world):
    api, base = world["api"], world["base"]
    assert api.post(f"{base}/integrations", json={"name": "All", "preset": "full"}).status_code == 422
    narrowed = api.post(f"{base}/integrations", json={"name": "Reader", "preset": "read_only"})
    assert narrowed.status_code == 201
    scopes = set(narrowed.json()["scopes"])
    assert "inbox.read" in scopes and not scopes & {"clients.read", "agents.read", "channels.read", "integrations.manage"}
    operator = api.patch(f"{base}/integrations/{narrowed.json()['id']}", json={"preset": "operator"})
    assert operator.status_code == 200 and "inbox.reply" in operator.json()["scopes"]
    assert api.patch(f"{base}/integrations/{narrowed.json()['id']}", json={"preset": "full"}).status_code == 422
    assert api.post(f"{base}/integrations", json={"name": "Typo", "scopes": ["nope.read"]}).status_code == 400
    assert api.post(f"{base}/integrations", json={"name": "Typo", "preset": "nope"}).status_code == 400


def test_an_agency_made_integration_with_wider_access_gets_no_token_from_the_portal(world):
    api, base = world["api"], world["base"]
    wide = _agency_integration(world, client_id=world["mine"]["id"], preset="full")
    assert [row["id"] for row in api.get(f"{base}/integrations").json()] == [wide["id"]]
    refused = api.post(f"{base}/integrations/{wide['id']}/tokens", json={"expires_in_days": 5})
    assert refused.status_code == 403
    with TestingSession() as db:
        assert db.query(ApiToken).count() == 0
    # Narrowing it to what the portal may grant is allowed, and then tokens are too.
    assert api.patch(f"{base}/integrations/{wide['id']}", json={"scopes": ["inbox.read"]}).status_code == 200
    assert api.post(f"{base}/integrations/{wide['id']}/tokens", json={"expires_in_days": 5}).status_code == 201


def test_a_portal_token_reaches_its_own_client_only_and_only_with_its_scopes(world):
    api = world["api"]
    integration = _make(world, scopes=["contacts.read"])
    token = _issue(world, integration["id"])["token"]
    headers = {"Authorization": f"Bearer {token}"}
    own = api.get(f"/api/v1/clients/{world['mine']['id']}/contacts", headers=headers)
    assert own.status_code == 200, own.text
    other = api.get(f"/api/v1/clients/{world['other']['id']}/contacts", headers=headers)
    assert other.status_code == 403
    assert api.get(f"/api/v1/clients/{world['mine']['id']}/conversations", headers=headers).status_code == 403
    assert api.get("/api/v1/clients", headers=headers).status_code == 403
    # It cannot mint further credentials: the integrations routes are not its scope.
    assert api.get("/api/integrations", headers=headers).status_code == 403


def test_token_expiry_rules_are_the_panels(world):
    integration = _make(world)
    path = f"{world['base']}/integrations/{integration['id']}/tokens"
    assert world["api"].post(path, json={"expires_in_days": 0}).status_code == 422
    assert world["api"].post(path, json={"expires_in_days": 1826}).status_code == 422
    issued = _issue(world, integration["id"], days=1825)
    assert datetime.fromisoformat(issued["expires_at"]) > datetime.now(timezone.utc)


# Caps -----------------------------------------------------------------------


def test_the_number_of_integrations_is_capped_per_client(world):
    for index in range(MAX_INTEGRATIONS):
        _make(world, name=f"Integration {index}")
    over = world["api"].post(f"{world['base']}/integrations", json={"name": "One more", "scopes": ["inbox.read"]})
    assert over.status_code == 409 and str(MAX_INTEGRATIONS) in over.json()["detail"]
    # The agency's own limit is not the portal's, and another client is unaffected.
    assert world["api"].post("/api/integrations", json={"name": "Agency", "preset": "read_only", "client_id": world["mine"]["id"]}).status_code == 201


def test_the_number_of_active_tokens_is_capped_and_revoking_frees_a_slot(world):
    integration = _make(world)
    for _ in range(MAX_TOKENS):
        _issue(world, integration["id"])
    path = f"{world['base']}/integrations/{integration['id']}/tokens"
    over = world["api"].post(path, json={"expires_in_days": 5})
    assert over.status_code == 409 and str(MAX_TOKENS) in over.json()["detail"]
    with TestingSession() as db:
        token_id = db.scalars(select(ApiToken.id).where(ApiToken.integration_id == uuid.UUID(integration["id"]))).first()
    assert world["api"].delete(f"{path}/{token_id}").status_code == 204
    assert world["api"].post(path, json={"expires_in_days": 5}).status_code == 201


def test_the_number_of_webhook_subscriptions_is_capped(world):
    integration = _make(world)
    path = f"{world['base']}/integrations/{integration['id']}/webhooks"
    for _ in range(MAX_WEBHOOKS):
        assert world["api"].post(path, json={"url": GOOD_URL, "events": ["message.received"]}).status_code == 201
    assert world["api"].post(path, json={"url": GOOD_URL, "events": ["message.received"]}).status_code == 409


# Webhook destinations -------------------------------------------------------


@pytest.mark.parametrize("url", [
    "http://hooks.example.com/x",          # not https
    "http://localhost:9000/x",             # the panel's loopback exception is not the portal's
    "https://localhost/x",
    "https://127.0.0.1/x",
    "https://169.254.169.254/latest/meta-data",
    "https://10.0.0.7/x",
    "https://service.internal.test/x",     # a name that resolves inside the network
    "https://unresolvable.invalid/x",
])
def test_a_portal_webhook_may_not_point_inside_the_network(world, url):
    integration = _make(world)
    response = world["api"].post(
        f"{world['base']}/integrations/{integration['id']}/webhooks", json={"url": url, "events": ["message.received"]},
    )
    assert response.status_code in (400, 422), f"{url}: {response.status_code}"
    with TestingSession() as db:
        assert db.query(WebhookSubscription).count() == 0


def test_the_panels_webhook_rules_are_unchanged(world):
    integration = _agency_integration(world)
    accepted = world["api"].post(
        f"/api/integrations/{integration['id']}/webhooks", json={"url": "http://localhost:9000/x", "events": ["message.received"]},
    )
    assert accepted.status_code == 201


def test_only_the_clients_own_events_and_only_known_events(world):
    integration = _make(world)
    path = f"{world['base']}/integrations/{integration['id']}/webhooks"
    assert world["api"].post(path, json={"url": GOOD_URL, "events": ["nope.event"]}).status_code == 400
    assert world["api"].post(path, json={"url": GOOD_URL, "events": []}).status_code == 422
    world["api"].post(path, json={"url": GOOD_URL, "events": ["message.received"]})
    with TestingSession() as db:
        # The integration is confined to the client, so an event of another client never fans out to it.
        mine, other = world["mine"]["id"], world["other"]["id"]
        agency = db.get(ApiIntegration, uuid.UUID(integration["id"])).agency_id
        assert outbound_webhooks.emit(db, agency_id=agency, client_id=other, event="message.received", data={}) == 0
        assert outbound_webhooks.emit(db, agency_id=agency, client_id=mine, event="message.received", data={}) == 1


def test_delivery_looks_at_the_address_again_for_portal_made_subscriptions(world, monkeypatch):
    integration = _make(world)
    hook = world["api"].post(
        f"{world['base']}/integrations/{integration['id']}/webhooks", json={"url": "https://moving.public.test/x", "events": ["message.received"]},
    )
    assert hook.status_code == 201
    sent = []

    async def fake_post(url, secret, payload):
        sent.append(url)
        return 200

    monkeypatch.setattr(outbound_webhooks, "_post", fake_post)
    with TestingSession() as db:
        subscription = db.get(WebhookSubscription, uuid.UUID(hook.json()["subscription_id"]))
        subscription.url = "https://moving.internal.test/x"  # the name was re-pointed after it was accepted
        db.add(WebhookDelivery(subscription_id=subscription.id, event="message.received", payload={}))
        db.commit()
        assert asyncio.run(outbound_webhooks.process_due(db)) == 1
        delivery = db.scalars(select(WebhookDelivery)).one()
        assert delivery.status == "pending" and "private or reserved" in delivery.last_error
    assert sent == []


# The panel and the public API do not change ---------------------------------


def test_the_panel_sees_everything_and_still_manages_oauth_and_the_full_preset(world):
    api = world["api"]
    portal_made = _make(world)
    wide = _agency_integration(world, name="Whole agency")
    foreign = _agency_integration(world, client_id=world["other"]["id"], preset="full")
    listed = {row["id"] for row in api.get("/api/integrations").json()}
    assert listed == {portal_made["id"], wide["id"], foreign["id"]}
    assert api.get(f"/api/integrations/{foreign['id']}/tokens").status_code == 200
    assert api.post(f"/api/integrations/{foreign['id']}/tokens", json={"expires_in_days": 5}).status_code == 201
    oauth = api.post(f"/api/integrations/{wide['id']}/oauth-client", json={"redirect_uris": ["https://a.example.com/cb"]})
    assert oauth.status_code == 201 and oauth.json()["client_secret"]
    # An agency-made integration for the client shows up in the portal's list with its own payload.
    with_client = _agency_integration(world, name="For Mine", client_id=world["mine"]["id"])
    portal_rows = {row["id"]: row for row in api.get(f"{world['base']}/integrations").json()}
    assert set(portal_rows) == {portal_made["id"], with_client["id"]}
    assert set(portal_rows[with_client["id"]]) == set(with_client)


def test_an_api_token_with_the_credentials_scope_still_reaches_the_panel_routes(world):
    api = world["api"]
    agency_token = _issue_agency(api)
    headers = {"Authorization": f"Bearer {agency_token}"}
    assert api.get("/api/integrations", headers=headers).status_code == 200
    assert api.get("/api/integrations/scopes", headers=headers).status_code == 200
    narrow = _agency_integration(world, name="Narrow")
    token = api.post(f"/api/integrations/{narrow['id']}/tokens", json={"expires_in_days": 5}).json()["token"]
    refused = api.get("/api/integrations", headers={"Authorization": f"Bearer {token}"})
    assert refused.status_code == 403 and "integrations.manage" in refused.json()["detail"]


def _issue_agency(api: TestClient) -> str:
    integration = api.post("/api/integrations", json={"name": "Agency admin", "preset": "full"}).json()
    return api.post(f"/api/integrations/{integration['id']}/tokens", json={"expires_in_days": 5}).json()["token"]
