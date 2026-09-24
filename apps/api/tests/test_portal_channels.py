"""Channel management from the client portal.

With a channel type switched on (``channels.whatsapp``, ``channels.whatsapp_cloud``,
``channels.instagram``, ``channels.messenger``, ``channels.webchat``), a client's
portal admins get the agency's own channel screens for that type under
``/api/portal/{slug}/manage``: the same handlers, confined to that one client.
These tests pin the door (type, role, session), the confinement (another
client's line, another client's agent), the way a hosted authorization comes
back to the portal and what the portal never sees.
"""

import re
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.deps import get_current_user
from app.models import Client, SocialOAuthState, WhatsAppChannel, WhatsAppCloudChannel
from app.portal_features import FEATURE_DISABLED_DETAIL
from app.portal_permissions import AGENTS_MANAGE, CHANNELS_MANAGE, has_permission, permissions_for
from app.routers import portal_manage
from app.security import encrypt_secret
from app.services import messaging_provider as provider_client
from app.services import whatsapp_cloud as whatsapp_cloud_service
from conftest import TestingSession
from test_whatsapp_evolution import _env, _stub_http

PASSWORD = "secure-portal"
FORBIDDEN = {"detail": "Your role cannot do this"}
FRONTEND = "https://app.example.test"

WHATSAPP = "channels.whatsapp"
CLOUD = "channels.whatsapp_cloud"
INSTAGRAM = "channels.instagram"
MESSENGER = "channels.messenger"
WEBCHAT = "channels.webchat"
TYPES = (WHATSAPP, CLOUD, INSTAGRAM, MESSENGER, WEBCHAT)

# Every channel (method, path) the portal mounts, relative to /api/portal/{slug}/manage.
WHATSAPP_ROUTES = {
    ("GET", "/whatsapp/clients/{client_id}/channels"), ("POST", "/whatsapp/clients/{client_id}/channels"),
    ("GET", "/whatsapp/channels/{ref}"), ("PUT", "/whatsapp/channels/{ref}"),
    ("DELETE", "/whatsapp/channels/{channel_id}"),
    ("POST", "/whatsapp/channels/{ref}/connect"), ("POST", "/whatsapp/channels/{ref}/disconnect"),
}
CLOUD_ROUTES = {
    ("GET", "/whatsapp-cloud/clients/{client_id}/channels"), ("POST", "/whatsapp-cloud/clients/{client_id}/channels"),
    ("PUT", "/whatsapp-cloud/channels/{ref}"), ("DELETE", "/whatsapp-cloud/channels/{channel_id}"),
    ("POST", "/whatsapp-cloud/channels/{ref}/connect"), ("POST", "/whatsapp-cloud/channels/{ref}/refresh"),
    ("POST", "/whatsapp-cloud/channels/{ref}/disconnect"),
}
WEBCHAT_ROUTES = {("GET", "/webchat/channels/{client_id}"), ("PUT", "/webchat/channels/{client_id}")}
SOCIAL_ROUTES = {
    ("GET", "/social/{provider}/clients/{client_id}/channels"),
    ("PATCH", "/social/{provider}/channels/{channel_id}"),
    ("POST", "/social/{provider}/channels/{ref}/connect"), ("POST", "/social/{provider}/channels/{ref}/disconnect"),
    ("POST", "/social/{provider}/oauth/start"),
    ("GET", "/social/{provider}/channels/{ref}/import-history"), ("POST", "/social/{provider}/channels/{ref}/import-history"),
}
CONFIG_ROUTE = ("GET", "/social/config")
CLIENT_ROUTE = ("GET", "/clients/{client_id}")
EXPECTED_ROUTES = WHATSAPP_ROUTES | CLOUD_ROUTES | WEBCHAT_ROUTES | SOCIAL_ROUTES | {CONFIG_ROUTE, CLIENT_ROUTE}
CHANNEL_PREFIXES = ("/whatsapp", "/webchat", "/social")

AGENT_PAYLOAD = {"provider": "openrouter", "model": "gpt-4.1-mini", "instructions": "", "personality": "", "is_active": True}
ACCOUNTS = [
    {"account_id": account, "platform": platform, "username": account, "display_name": account.upper(), "phone_number": None,
     "is_active": True, "profile_id": "prof-x", "raw": {}}
    for account, platform in (
        ("ig-mine", "instagram"), ("ig-other", "instagram"), ("ig-new", "instagram"),
        ("fb-mine", "facebook"), ("fb-other", "facebook"),
    )
]


def _fill(path: str, **known: str) -> str:
    """A mounted path with every parameter filled: the ones given, random ids for the rest."""
    values = {"provider": "instagram", **known}
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1)) or str(uuid.uuid4()), path)


def _switch(api: TestClient, customer: dict, **features: bool) -> None:
    response = api.patch(f"/api/clients/{customer['id']}/portal", json={"portal_features": features})
    assert response.status_code == 200, response.text


def _sign_in(api: TestClient, customer: dict, email: str) -> None:
    response = api.post(f"/api/portal/{customer['portal_slug']}/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text


def _person(api: TestClient, customer: dict, email: str, role: str) -> None:
    created = api.post(
        f"/api/clients/{customer['id']}/portal-users",
        json={"name": email.split("@")[0].title(), "email": email, "password": PASSWORD, "role": role},
    )
    assert created.status_code == 201, created.text


def _agent(api: TestClient, customer: dict, name: str) -> dict:
    created = api.post("/api/agents", json={**AGENT_PAYLOAD, "client_id": customer["id"], "name": name})
    assert created.status_code == 201, created.text
    return created.json()


def _connect_social(api: TestClient, customer: dict, agent: dict, provider: str, account: str) -> dict:
    started = api.post(f"/api/social/{provider}/oauth/start", json={"client_id": customer["id"], "agent_id": agent["id"]})
    assert started.status_code == 200, started.text
    pending = api.get(f"/api/social/{provider}/oauth/pending", params={"client_id": customer["id"]})
    assert pending.status_code == 200, pending.text
    done = api.post(f"/api/social/{provider}/oauth/complete", json={"setup_id": pending.json()["setup_id"], "external_account_id": account})
    assert done.status_code == 200, done.text
    return done.json()


@pytest.fixture
def world(authenticated_client: TestClient, monkeypatch) -> dict:
    """Two clients of one agency with every channel type, lines on both.

    The agency and the first client's admin are signed in on the one test
    client; ``_as_helper`` swaps the portal session for the agent role's.
    """
    api = authenticated_client
    _env(monkeypatch)
    _stub_http(monkeypatch)
    monkeypatch.setattr(get_settings(), "frontend_url", FRONTEND)
    monkeypatch.setattr(get_settings(), "social_public_url", FRONTEND)

    from app.services import messaging_profiles as profiles

    async def _ensure(db, client):
        client.provider_profile_id = client.provider_profile_id or f"prof-{str(client.id)[:8]}"
        db.commit()
        return client.provider_profile_id

    monkeypatch.setattr(profiles, "ensure_client_profile", _ensure)
    monkeypatch.setattr(profiles, "ensure_channel_profile", AsyncMock(return_value="prof-cloud"))
    monkeypatch.setattr(provider_client, "connect_url", AsyncMock(return_value={"authorization_url": "https://hosted.example/connect/1"}))
    monkeypatch.setattr(provider_client, "list_accounts", AsyncMock(return_value=ACCOUNTS))
    monkeypatch.setattr(provider_client, "require_account", AsyncMock(return_value=ACCOUNTS[0]))

    api.put("/api/providers/openrouter", json={"api_key": "sk-or-agency-secret"})
    mine = api.post("/api/clients", json={"name": "Mine Co", "is_active": True}).json()
    other = api.post("/api/clients", json={"name": "Other Co", "is_active": True}).json()
    _person(api, mine, "boss@mine.co", "admin")
    _person(api, mine, "helper@mine.co", "agent")
    assert api.patch(f"/api/clients/{mine['id']}/portal", json={"portal_enabled": True}).status_code == 200
    _switch(api, mine, **{key: True for key in TYPES})

    agent, foreign_agent = _agent(api, mine, "Mine agent"), _agent(api, other, "Other agent")
    lines = {}
    for name, customer, ag in (("mine", mine, agent), ("other", other, foreign_agent)):
        lines[f"wa_{name}"] = [
            api.post(f"/api/whatsapp/clients/{customer['id']}/channels", json={"agent_id": ag["id"], "label": label}).json()
            for label in (("Ventas", "Soporte") if name == "mine" else ("Ajena",))
        ]
        lines[f"cloud_{name}"] = api.post(f"/api/whatsapp-cloud/clients/{customer['id']}/channels", json={"agent_id": ag["id"], "label": "API"}).json()
        widget = api.put(f"/api/webchat/channels/{customer['id']}", json={"agent_id": ag["id"], "greeting": "Hi", "color": "#2f6df0"})
        assert widget.status_code == 200, widget.text
        lines[f"widget_{name}"] = widget.json()
        lines[f"instagram_{name}"] = _connect_social(api, customer, ag, "instagram", f"ig-{name}")
        lines[f"messenger_{name}"] = _connect_social(api, customer, ag, "messenger", f"fb-{name}")
    _sign_in(api, mine, "boss@mine.co")
    return {
        "api": api, "mine": mine, "other": other, "agent": agent, "foreign_agent": foreign_agent,
        "base": f"/api/portal/{mine['portal_slug']}/manage", **lines,
    }


def _as_helper(world: dict) -> None:
    _sign_in(world["api"], world["mine"], "helper@mine.co")


def _states(**where) -> list[SocialOAuthState]:
    with TestingSession() as db:
        return list(db.scalars(select(SocialOAuthState).filter_by(**where).order_by(SocialOAuthState.created_at)))


# The door -------------------------------------------------------------------


def test_only_admins_hold_the_permission():
    assert has_permission("admin", CHANNELS_MANAGE)
    assert not has_permission("agent", CHANNELS_MANAGE)
    assert CHANNELS_MANAGE in permissions_for("admin") and CHANNELS_MANAGE not in permissions_for("agent")
    assert CHANNELS_MANAGE != AGENTS_MANAGE


def test_the_mounted_channel_surface_is_exactly_the_channel_screens_and_none_takes_a_panel_user():
    mounted = set()
    for route in portal_manage.router.routes:
        path = route.path.removeprefix("/portal/{slug}/manage")
        if not (path.startswith(CHANNEL_PREFIXES) or path == "/clients/{client_id}"):
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


def test_without_a_session_every_mounted_route_is_a_401(world):
    api = world["api"]
    api.cookies.delete("portal_access_token")
    for method, path in sorted(EXPECTED_ROUTES):
        response = api.request(method, world["base"] + _fill(path))
        assert response.status_code == 401, f"{method} {path}: {response.status_code}"


def test_the_agent_role_is_refused_everywhere(world):
    _as_helper(world)
    for method, path in sorted(EXPECTED_ROUTES):
        response = world["api"].request(method, world["base"] + _fill(path), json={})
        assert response.status_code == 403, f"{method} {path}: {response.status_code}"
        assert response.json() == FORBIDDEN, f"{method} {path}"


def _routes_of(feature: str) -> list[tuple[str, str]]:
    if feature == WHATSAPP:
        return sorted(WHATSAPP_ROUTES)
    if feature == CLOUD:
        return sorted(CLOUD_ROUTES)
    if feature == WEBCHAT:
        return sorted(WEBCHAT_ROUTES)
    return sorted(SOCIAL_ROUTES)


@pytest.mark.parametrize("feature", TYPES)
def test_a_type_that_is_off_is_refused_on_its_routes_while_the_other_types_still_work(world, feature):
    api, base, mine = world["api"], world["base"], world["mine"]
    _switch(api, mine, **{feature: False})
    provider = "messenger" if feature == MESSENGER else "instagram"
    for method, path in _routes_of(feature):
        if path.startswith("/social") and feature == INSTAGRAM and provider != "instagram":
            continue
        response = api.request(method, base + _fill(path, provider=provider, client_id=mine["id"]), json={})
        assert response.status_code == 403, f"{method} {path}: {response.status_code}"
        assert response.json() == {"detail": FEATURE_DISABLED_DETAIL}, f"{method} {path}"
    # Every other type answers as before.
    others = {
        WHATSAPP: f"/whatsapp/clients/{mine['id']}/channels",
        CLOUD: f"/whatsapp-cloud/clients/{mine['id']}/channels",
        INSTAGRAM: f"/social/instagram/clients/{mine['id']}/channels",
        MESSENGER: f"/social/messenger/clients/{mine['id']}/channels",
        WEBCHAT: f"/webchat/channels/{mine['id']}",
    }
    for key, path in others.items():
        if key != feature:
            assert api.get(base + path).status_code == 200, key
    # The client's own record still opens while any type is on.
    assert api.get(f"{base}/clients/{mine['id']}").status_code == 200


def test_social_types_are_told_apart_by_the_provider_in_the_path(world):
    api, base, mine = world["api"], world["base"], world["mine"]
    _switch(api, mine, **{INSTAGRAM: False})
    assert api.get(f"{base}/social/instagram/clients/{mine['id']}/channels").status_code == 403
    assert api.get(f"{base}/social/messenger/clients/{mine['id']}/channels").status_code == 200
    refused = api.post(f"{base}/social/instagram/oauth/start", json={"client_id": mine["id"], "agent_id": world["agent"]["id"]})
    assert refused.status_code == 403 and refused.json() == {"detail": FEATURE_DISABLED_DETAIL}
    # A provider that is not one of ours has no type to switch on.
    assert api.get(f"{base}/social/telegram/clients/{mine['id']}/channels").status_code == 404


def test_the_lists_show_only_the_types_switched_on(world):
    api, base, mine = world["api"], world["base"], world["mine"]
    assert set(api.get(f"{base}/social/config").json()) == {"instagram", "messenger"}
    _switch(api, mine, **{MESSENGER: False})
    assert set(api.get(f"{base}/social/config").json()) == {"instagram"}
    _switch(api, mine, **{INSTAGRAM: False})
    assert api.get(f"{base}/social/config").status_code == 403
    assert api.get(f"{base}/clients/{mine['id']}").status_code == 200  # whatsapp, api number and web chat are still on
    _switch(api, mine, **{key: False for key in TYPES})
    for path in (f"/social/config", f"/clients/{mine['id']}"):
        assert api.get(base + path).json() == {"detail": FEATURE_DISABLED_DETAIL}, path
    # The agents function is a different door: it does not open the channels.
    _switch(api, mine, agents=True)
    assert api.get(f"{base}/whatsapp/clients/{mine['id']}/channels").status_code == 403
    assert api.get(f"{base}/agents").status_code == 200


def test_a_type_opens_all_of_its_lines_and_switching_it_off_only_removes_the_portals_access(world):
    api, base, mine = world["api"], world["base"], world["mine"]
    first, second = world["wa_mine"]
    with TestingSession() as db:
        for line in (first, second):
            row = db.get(WhatsAppChannel, uuid.UUID(line["id"]))
            row.status, row.is_enabled = "connected", True
        db.commit()
    listed = api.get(f"{base}/whatsapp/clients/{mine['id']}/channels").json()
    assert [row["id"] for row in listed] == [first["id"], second["id"]]
    assert api.get(f"{base}/whatsapp/channels/{second['id']}").status_code == 200

    _switch(api, mine, **{WHATSAPP: False})
    for line in (first, second):
        assert api.get(f"{base}/whatsapp/channels/{line['id']}").status_code == 403
        assert api.post(f"{base}/whatsapp/channels/{line['id']}/disconnect").status_code == 403
        assert api.delete(f"{base}/whatsapp/channels/{line['id']}").status_code == 403
    # Nothing was disconnected: the agency still sees both lines connected and enabled.
    for line in (first, second):
        panel = api.get(f"/api/whatsapp/channels/{line['id']}").json()
        assert panel["status"] == "connected" and panel["is_enabled"] is True
    _switch(api, mine, **{WHATSAPP: True})
    assert len(api.get(f"{base}/whatsapp/clients/{mine['id']}/channels").json()) == 2


# What an admin does ---------------------------------------------------------


def test_an_admin_runs_a_whatsapp_line_with_the_panels_shapes(world):
    api, base, mine, agent = world["api"], world["base"], world["mine"], world["agent"]
    listed = api.get(f"{base}/whatsapp/clients/{mine['id']}/channels")
    assert listed.status_code == 200
    assert listed.json() == api.get(f"/api/whatsapp/clients/{mine['id']}/channels").json()

    created = api.post(f"{base}/whatsapp/clients/{mine['id']}/channels", json={"agent_id": agent["id"], "label": "Nueva"})
    assert created.status_code == 201, created.text
    line = created.json()
    assert line["client_id"] == mine["id"] and set(line) == set(api.get(f"/api/whatsapp/channels/{line['id']}").json())

    configured = api.put(f"{base}/whatsapp/channels/{line['id']}", json={"agent_id": agent["id"], "label": "Renombrada", "groups_enabled": False})
    assert configured.status_code == 200 and configured.json()["label"] == "Renombrada"

    connected = api.post(f"{base}/whatsapp/channels/{line['id']}/connect")
    assert connected.status_code == 200, connected.text
    assert connected.json()["status"] == "qr" and connected.json()["qr_code"] == "data:image/png;base64,QR1"  # the QR is meant to be shown
    assert api.get(f"{base}/whatsapp/channels/{line['id']}").json()["qr_code"] == "data:image/png;base64,QR1"

    disconnected = api.post(f"{base}/whatsapp/channels/{line['id']}/disconnect")
    assert disconnected.status_code == 200
    assert api.delete(f"{base}/whatsapp/channels/{line['id']}").status_code == 204
    assert api.get(f"{base}/whatsapp/channels/{line['id']}").status_code == 404
    assert api.get(f"/api/whatsapp/channels/{line['id']}").status_code == 404


def test_an_admin_runs_a_whatsapp_api_number_with_the_panels_shapes(world, monkeypatch):
    api, base, mine, agent = world["api"], world["base"], world["mine"], world["agent"]
    listed = api.get(f"{base}/whatsapp-cloud/clients/{mine['id']}/channels")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [world["cloud_mine"]["id"]]

    created = api.post(f"{base}/whatsapp-cloud/clients/{mine['id']}/channels", json={"agent_id": agent["id"], "label": "Otro"})
    assert created.status_code == 201, created.text
    number = created.json()
    assert set(number) == set(api.get(f"/api/whatsapp-cloud/channels/{number['id']}").json())
    renamed = api.put(f"{base}/whatsapp-cloud/channels/{number['id']}", json={"agent_id": agent["id"], "label": "Renombrado"})
    assert renamed.status_code == 200 and renamed.json()["label"] == "Renombrado"

    hosted = api.post(f"{base}/whatsapp-cloud/channels/{number['id']}/connect")
    assert hosted.status_code == 200, hosted.text
    assert hosted.json()["connect_url"] == "https://hosted.example/connect/1"  # the page to open is meant to be shown

    monkeypatch.setattr("app.routers.whatsapp_cloud.verify_account", AsyncMock(return_value={
        "display_phone_number": "+57 300 111 2233", "verified_name": "Mine", "quality_rating": "GREEN", "messaging_limit": "TIER_1K"}))
    with TestingSession() as db:
        db.get(WhatsAppCloudChannel, uuid.UUID(number["id"])).external_account_id = "acct-9"
        db.commit()
    refreshed = api.post(f"{base}/whatsapp-cloud/channels/{number['id']}/refresh")
    assert refreshed.status_code == 200 and refreshed.json()["status"] == "connected"
    assert refreshed.json()["phone_number"] == "+57 300 111 2233" and refreshed.json()["quality_rating"] == "GREEN"
    assert api.post(f"{base}/whatsapp-cloud/channels/{number['id']}/disconnect").json()["status"] == "disconnected"
    assert api.delete(f"{base}/whatsapp-cloud/channels/{number['id']}").status_code == 204
    assert api.get(f"/api/whatsapp-cloud/channels/{number['id']}").status_code == 404


def test_an_admin_runs_the_web_chat(world):
    api, base, mine, agent = world["api"], world["base"], world["mine"], world["agent"]
    shown = api.get(f"{base}/webchat/channels/{mine['id']}")
    assert shown.status_code == 200 and shown.json() == api.get(f"/api/webchat/channels/{mine['id']}").json()
    saved = api.put(f"{base}/webchat/channels/{mine['id']}", json={"agent_id": agent["id"], "is_enabled": False, "greeting": "Hola", "color": "#111111", "position": "left"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["greeting"] == "Hola" and saved.json()["is_enabled"] is False
    assert saved.json()["public_id"] == shown.json()["public_id"]  # the embedded snippet survives every edit
    assert api.get(f"/api/webchat/channels/{mine['id']}").json()["position"] == "left"


@pytest.mark.parametrize("provider", ["instagram", "messenger"])
def test_an_admin_runs_an_instagram_or_messenger_account(world, provider):
    api, base, mine, agent = world["api"], world["base"], world["mine"], world["agent"]
    account = world[f"{provider}_mine"]
    listed = api.get(f"{base}/social/{provider}/clients/{mine['id']}/channels")
    assert listed.status_code == 200 and [row["id"] for row in listed.json()] == [account["id"]]

    renamed = api.patch(f"{base}/social/{provider}/channels/{account['id']}", json={"agent_id": agent["id"], "label": "Tienda"})
    assert renamed.status_code == 200 and renamed.json()["label"] == "Tienda"
    verified = api.post(f"{base}/social/{provider}/channels/{account['id']}/connect")
    assert verified.status_code == 200 and verified.json()["status"] == "connected"

    assert api.get(f"{base}/social/{provider}/channels/{account['id']}/import-history").status_code == 404
    imported = api.post(f"{base}/social/{provider}/channels/{account['id']}/import-history", json={})
    assert imported.status_code == 202, imported.text
    assert api.get(f"{base}/social/{provider}/channels/{account['id']}/import-history").json()["status"] == "pending"

    started = api.post(f"{base}/social/{provider}/oauth/start", json={"client_id": mine["id"], "agent_id": agent["id"]})
    assert started.status_code == 200 and started.json() == {"authorization_url": "https://hosted.example/connect/1"}

    assert api.post(f"{base}/social/{provider}/channels/{account['id']}/disconnect").status_code == 204
    assert api.get(f"{base}/social/{provider}/clients/{mine['id']}/channels").json() == []
    assert api.get(f"/api/social/{provider}/clients/{mine['id']}/channels").json() == []


def test_the_clients_record_offers_its_own_agents_only(world):
    api, base, mine, other = world["api"], world["base"], world["mine"], world["other"]
    record = api.get(f"{base}/clients/{mine['id']}")
    assert record.status_code == 200
    assert [a["id"] for a in record.json()["agents"]] == [world["agent"]["id"]]
    assert record.json() == api.get(f"/api/clients/{mine['id']}").json()
    assert api.get(f"{base}/clients/{other['id']}").status_code == 404
    # The panel still sees both clients and both agents.
    assert len(api.get("/api/clients").json()) == 2


# Confinement ----------------------------------------------------------------


def test_another_clients_line_is_a_404_on_every_mounted_route(world):
    api, base, other = world["api"], world["base"], world["other"]
    foreign = {
        "wa": world["wa_other"][0]["id"], "cloud": world["cloud_other"]["id"],
        "instagram": world["instagram_other"]["id"], "messenger": world["messenger_other"]["id"],
    }
    body = {"agent_id": world["foreign_agent"]["id"], "label": "Hijacked"}
    attempts = [
        ("GET", f"/whatsapp/channels/{foreign['wa']}", None),
        ("PUT", f"/whatsapp/channels/{foreign['wa']}", body),
        ("DELETE", f"/whatsapp/channels/{foreign['wa']}", None),
        ("POST", f"/whatsapp/channels/{foreign['wa']}/connect", None),
        ("POST", f"/whatsapp/channels/{foreign['wa']}/disconnect", None),
        # A client id in place of a line id reaches that client's first line; it must not.
        ("GET", f"/whatsapp/channels/{other['id']}", None),
        ("PUT", f"/whatsapp/channels/{other['id']}", body),
        ("POST", f"/whatsapp/channels/{other['id']}/connect", None),
        ("GET", f"/whatsapp/clients/{other['id']}/channels", None),
        ("POST", f"/whatsapp/clients/{other['id']}/channels", body),
        ("PUT", f"/whatsapp-cloud/channels/{foreign['cloud']}", body),
        ("DELETE", f"/whatsapp-cloud/channels/{foreign['cloud']}", None),
        ("POST", f"/whatsapp-cloud/channels/{foreign['cloud']}/connect", None),
        ("POST", f"/whatsapp-cloud/channels/{foreign['cloud']}/refresh", None),
        ("POST", f"/whatsapp-cloud/channels/{foreign['cloud']}/disconnect", None),
        ("PUT", f"/whatsapp-cloud/channels/{other['id']}", body),
        ("POST", f"/whatsapp-cloud/channels/{other['id']}/connect", None),
        ("GET", f"/whatsapp-cloud/clients/{other['id']}/channels", None),
        ("POST", f"/whatsapp-cloud/clients/{other['id']}/channels", body),
        ("GET", f"/webchat/channels/{other['id']}", None),
        ("PUT", f"/webchat/channels/{other['id']}", {"agent_id": world["foreign_agent"]["id"], "greeting": "Hijacked"}),
        ("GET", f"/clients/{other['id']}", None),
    ]
    for provider in ("instagram", "messenger"):
        channel = foreign[provider]
        attempts += [
            ("GET", f"/social/{provider}/clients/{other['id']}/channels", None),
            ("PATCH", f"/social/{provider}/channels/{channel}", {"label": "Hijacked"}),
            ("POST", f"/social/{provider}/channels/{channel}/connect", None),
            ("POST", f"/social/{provider}/channels/{channel}/disconnect", None),
            ("POST", f"/social/{provider}/channels/{other['id']}/connect", None),
            ("GET", f"/social/{provider}/channels/{channel}/import-history", None),
            ("POST", f"/social/{provider}/channels/{channel}/import-history", {}),
            ("POST", f"/social/{provider}/oauth/start", {"client_id": other["id"], "agent_id": world["foreign_agent"]["id"]}),
        ]
    for method, path, payload in attempts:
        response = api.request(method, base + path, json=payload)
        assert response.status_code == 404, f"{method} {path}: {response.status_code} {response.text}"

    # Nothing of the other client changed or left it.
    assert [row["label"] for row in api.get(f"/api/whatsapp/clients/{other['id']}/channels").json()] == ["Ajena"]
    assert api.get(f"/api/whatsapp-cloud/channels/{foreign['cloud']}").json()["label"] == "API"
    assert api.get(f"/api/webchat/channels/{other['id']}").json()["greeting"] == "Hi"
    for provider in ("instagram", "messenger"):
        assert [row["label"] for row in api.get(f"/api/social/{provider}/clients/{other['id']}/channels").json()] == [None]
    assert len(api.get(f"/api/whatsapp/clients/{other['id']}/channels").json()) == 1


def test_an_agent_of_another_client_cannot_be_put_on_a_line(world):
    api, base, mine = world["api"], world["base"], world["mine"]
    foreign = world["foreign_agent"]["id"]
    line = world["wa_mine"][0]["id"]
    assert api.post(f"{base}/whatsapp/clients/{mine['id']}/channels", json={"agent_id": foreign}).status_code == 400
    assert api.put(f"{base}/whatsapp/channels/{line}", json={"agent_id": foreign}).status_code == 400
    assert api.post(f"{base}/whatsapp-cloud/clients/{mine['id']}/channels", json={"agent_id": foreign}).status_code == 400
    assert api.put(f"{base}/webchat/channels/{mine['id']}", json={"agent_id": foreign}).status_code == 400
    account = world["instagram_mine"]["id"]
    assert api.patch(f"{base}/social/instagram/channels/{account}", json={"agent_id": foreign}).status_code == 400
    assert api.post(f"{base}/social/instagram/oauth/start", json={"client_id": mine["id"], "agent_id": foreign}).status_code == 400
    assert api.get(f"/api/whatsapp/channels/{line}").json()["agent_id"] == world["agent"]["id"]


def test_another_agency_and_its_sessions_are_shut_out(world):
    api, base = world["api"], world["base"]
    from app.models import Agency, Agent, PortalUser
    from app.security import hash_password

    with TestingSession() as db:
        agency = Agency(name="Rival agency", slug="rival-agency")
        db.add(agency)
        db.flush()
        rival = Client(agency_id=agency.id, name="Rival Co", portal_slug="rival-co", portal_enabled=True,
                       portal_features={key: True for key in TYPES})
        db.add(rival)
        db.flush()
        db.add(PortalUser(client_id=rival.id, name="Rita", email="rita@rival.co", role="admin", password_hash=hash_password(PASSWORD)))
        rival_agent = Agent(agency_id=agency.id, client_id=rival.id, name="Rival agent")
        db.add(rival_agent)
        db.flush()
        rival_line = WhatsAppChannel(agency_id=agency.id, client_id=rival.id, agent_id=rival_agent.id)
        db.add(rival_line)
        db.commit()
        rival_id, rival_line_id = str(rival.id), str(rival_line.id)

    assert api.get(f"{base}/whatsapp/channels/{rival_line_id}").status_code == 404
    assert api.get(f"{base}/whatsapp/clients/{rival_id}/channels").status_code == 404
    # Their session does not open my slug, and on their own slug my lines are not theirs.
    assert api.post("/api/portal/rival-co/login", json={"email": "rita@rival.co", "password": PASSWORD}).status_code == 200
    assert api.get(f"{base}/whatsapp/clients/{world['mine']['id']}/channels").status_code == 401
    theirs = "/api/portal/rival-co/manage"
    assert [row["id"] for row in api.get(f"{theirs}/whatsapp/clients/{rival_id}/channels").json()] == [rival_line_id]
    assert api.get(f"{theirs}/whatsapp/channels/{world['wa_mine'][0]['id']}").status_code == 404
    assert api.get(f"{theirs}/whatsapp/clients/{world['mine']['id']}/channels").status_code == 404


def test_an_agency_session_alone_does_not_open_the_portal_routes(world):
    api = world["api"]
    api.cookies.delete("portal_access_token")
    assert api.get(f"/api/whatsapp/clients/{world['mine']['id']}/channels").status_code == 200
    assert api.get(f"{world['base']}/whatsapp/clients/{world['mine']['id']}/channels").status_code == 401


def test_nothing_else_of_the_channel_routers_is_reachable_from_the_portal(world):
    api, base, mine = world["api"], world["base"], world["mine"]
    account, number = world["instagram_mine"]["id"], world["cloud_mine"]["id"]
    closed = [
        ("GET", f"/social/instagram/channels/{account}"),
        ("PUT", f"/social/instagram/channels/{account}"),
        ("GET", f"/social/instagram/oauth/pending?client_id={mine['id']}"),
        ("POST", "/social/instagram/oauth/complete"),
        ("GET", f"/whatsapp-cloud/channels/{number}"),
        ("GET", f"/whatsapp-cloud/clients/{mine['id']}/templates"),
        ("GET", "/internal/whatsapp/channels"),
        ("POST", "/webhook/ensure"),
    ]
    for method, path in closed:
        response = api.request(method, base + path, json={})
        assert response.status_code in (404, 405), f"{method} {path}: {response.status_code}"


# Where a hosted authorization comes back to ---------------------------------


def _callback(api: TestClient, **params) -> str:
    response = api.get("/api/public/messaging/connect/callback", params=params, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers["location"]


def test_a_social_connection_started_in_the_portal_returns_to_the_portal(world):
    api, base, mine, agent = world["api"], world["base"], world["mine"], world["agent"]
    slug = mine["portal_slug"]
    assert api.post(f"{base}/social/instagram/oauth/start", json={"client_id": mine["id"], "agent_id": agent["id"]}).status_code == 200
    state = _states(client_id=uuid.UUID(mine["id"]), provider="instagram")[-1]
    assert state.user_id is None and state.next_url == f"{FRONTEND}/portal/{slug}/channels/instagram"
    # The web names its own screen; the one the server rebuilds is the same.
    api.post(f"{base}/social/instagram/oauth/start", json={"client_id": mine["id"], "agent_id": agent["id"], "next_path": f"/portal/{slug}/channels/instagram"})
    assert _states(client_id=uuid.UUID(mine["id"]), provider="instagram")[-1].next_url == f"{FRONTEND}/portal/{slug}/channels/instagram"

    profile = api.get(f"/api/clients/{mine['id']}").json()
    with TestingSession() as db:
        profile_id = db.get(Client, uuid.UUID(profile["id"])).provider_profile_id
    landed = _callback(api, profileId=profile_id, accountId="ig-new", platform="instagram")
    assert landed.startswith(f"{FRONTEND}/portal/{slug}/channels/instagram?")
    assert "messaging_status=ready" in landed and "line=" in landed
    assert len(api.get(f"{base}/social/instagram/clients/{mine['id']}/channels").json()) == 2
    # A refused approval comes back to the portal too.
    api.post(f"{base}/social/instagram/oauth/start", json={"client_id": mine["id"], "agent_id": agent["id"]})
    refused = _callback(api, profileId=profile_id, error="access_denied")
    assert refused.startswith(f"{FRONTEND}/portal/{slug}/channels/instagram?") and "messaging_status=error" in refused


def test_the_agencys_own_social_flow_still_returns_to_the_panel(world):
    api, mine, agent = world["api"], world["mine"], world["agent"]
    api.post("/api/social/instagram/oauth/start", json={"client_id": mine["id"], "agent_id": agent["id"]})
    state = _states(client_id=uuid.UUID(mine["id"]), provider="instagram")[-1]
    assert state.user_id is not None and state.next_url == f"{FRONTEND}/clients/{mine['id']}/channels/instagram"
    with TestingSession() as db:
        profile_id = db.get(Client, uuid.UUID(mine["id"])).provider_profile_id
    landed = _callback(api, profileId=profile_id, accountId="ig-new", platform="instagram")
    assert landed.startswith(f"{FRONTEND}/clients/{mine['id']}/channels/instagram?")


def test_a_return_path_from_the_portal_must_be_this_clients_own_screen(world):
    api, base, mine, agent = world["api"], world["base"], world["mine"], world["agent"]
    slug = mine["portal_slug"]
    body = {"client_id": mine["id"], "agent_id": agent["id"]}
    before = len(_states())
    for bad in (
        f"/clients/{mine['id']}/channels/instagram",  # the panel's screen is not the portal's
        f"/portal/other-slug/channels/instagram",
        f"/portal/{slug}/channels/messenger",  # another type's screen
        f"/portal/{slug}/channels/instagram/../../../evil",
        f"/portal/{slug}/channels/instagram?x=1",
        "//evil.test", "https://evil.test/portal", "https://evil.test", "/channels/instagram",
    ):
        response = api.post(f"{base}/social/instagram/oauth/start", json={**body, "next_path": bad})
        assert response.status_code == 400, f"{bad}: {response.status_code}"
    assert len(_states()) == before  # nothing was stored for a refused target

    # The client's own domain serves the portal from the root, once it is verified.
    assert api.post(f"{base}/social/instagram/oauth/start", json={**body, "next_path": "/channels/instagram"}).status_code == 400
    with TestingSession() as db:
        row = db.get(Client, uuid.UUID(mine["id"]))
        row.portal_domain, row.portal_domain_verified = "portal.mine.test", True
        db.commit()
    assert api.post(f"{base}/social/instagram/oauth/start", json={**body, "next_path": "/channels/instagram"}).status_code == 200
    assert _states(client_id=uuid.UUID(mine["id"]), provider="instagram")[-1].next_url == "https://portal.mine.test/channels/instagram"
    assert api.post(f"{base}/social/instagram/oauth/start", json={**body, "next_path": "/channels/messenger"}).status_code == 400


def _unlink(number: str) -> None:
    with TestingSession() as db:
        db.get(WhatsAppCloudChannel, uuid.UUID(number)).external_account_id = ""
        db.commit()


def test_a_whatsapp_api_connection_started_in_the_portal_returns_to_the_portal(world, monkeypatch):
    api, base, mine = world["api"], world["base"], world["mine"]
    slug, number = mine["portal_slug"], world["cloud_mine"]["id"]
    monkeypatch.setattr(whatsapp_cloud_service, "verify_account", AsyncMock(return_value={
        "display_phone_number": "+99", "verified_name": "Mine", "quality_rating": "GREEN", "messaging_limit": "TIER_1K"}))
    with TestingSession() as db:
        db.get(WhatsAppCloudChannel, uuid.UUID(number)).provider_profile_id = "prof-cloud"
        db.commit()
    portal_screen = f"{FRONTEND}/portal/{slug}/channels/whatsapp-cloud"
    panel_screen = f"{FRONTEND}/clients/{mine['id']}/channels/whatsapp-cloud"

    assert api.post(f"{base}/whatsapp-cloud/channels/{number}/connect").json()["connect_url"] == "https://hosted.example/connect/1"
    state = _states(client_id=uuid.UUID(mine["id"]), provider="whatsapp_cloud")[-1]
    assert state.user_id is None and state.next_url == portal_screen and state.used_at is None
    landed = _callback(api, profileId="prof-cloud", accountId="acct-9", connected="whatsapp")
    assert landed.startswith(f"{portal_screen}?") and "messaging_status=ready" in landed and f"line={number}" in landed
    # The return is spent: the same callback again is the panel's.
    assert _callback(api, profileId="prof-cloud", accountId="acct-9", connected="whatsapp").startswith(f"{panel_screen}?")

    # A refused approval comes back to the portal too (the number is unlinked again first,
    # since a linked number is verified on connect instead of sent to the hosted page).
    _unlink(number)
    api.post(f"{base}/whatsapp-cloud/channels/{number}/connect")
    assert _callback(api, profileId="prof-cloud", error="access_denied").startswith(f"{portal_screen}?")

    # Whoever started the latest flow decides: the agency starting one retires the portal's return.
    _unlink(number)
    api.post(f"{base}/whatsapp-cloud/channels/{number}/connect")
    assert api.post(f"/api/whatsapp-cloud/channels/{number}/connect").status_code == 200
    landed = _callback(api, profileId="prof-cloud", accountId="acct-9", connected="whatsapp")
    assert landed.startswith(f"{panel_screen}?")


def test_the_agencys_own_whatsapp_api_flow_is_unchanged(world, monkeypatch):
    api, mine, number = world["api"], world["mine"], world["cloud_mine"]["id"]
    monkeypatch.setattr(whatsapp_cloud_service, "verify_account", AsyncMock(return_value={
        "display_phone_number": "+99", "verified_name": "Mine", "quality_rating": None, "messaging_limit": None}))
    with TestingSession() as db:
        db.get(WhatsAppCloudChannel, uuid.UUID(number)).provider_profile_id = "prof-cloud"
        db.commit()
    assert api.post(f"/api/whatsapp-cloud/channels/{number}/connect").status_code == 200
    assert _states(provider="whatsapp_cloud") == []
    landed = _callback(api, profileId="prof-cloud", accountId="acct-9", connected="whatsapp")
    assert landed.startswith(f"{FRONTEND}/clients/{mine['id']}/channels/whatsapp-cloud?")


# Secrets --------------------------------------------------------------------


def test_the_portal_never_sees_provider_ids_or_tokens_the_screens_do_not_draw(world):
    api, base, mine = world["api"], world["base"], world["mine"]
    with TestingSession() as db:
        number = db.get(WhatsAppCloudChannel, uuid.UUID(world["cloud_mine"]["id"]))
        number.phone_number_id, number.waba_id, number.external_account_id = "pn-123456", "waba-654321", "acct-77"
        number.provider_profile_id = "prof-secret-profile"
        line = db.get(WhatsAppChannel, uuid.UUID(world["wa_mine"][0]["id"]))
        line.encrypted_auth_state = encrypt_secret("SESSION-STATE-SECRET")
        db.commit()
        verify_token = number.webhook_verify_token

    panel = api.get(f"/api/whatsapp-cloud/clients/{mine['id']}/channels").json()[0]
    assert panel["webhook_verify_token"] == verify_token and panel["waba_id"] == "waba-654321"

    numbers = api.get(f"{base}/whatsapp-cloud/clients/{mine['id']}/channels")
    assert numbers.status_code == 200
    row = numbers.json()[0]
    assert set(row) == set(panel)  # the shape is the panel's; only the values are blanked
    assert row["webhook_verify_token"] == "" and row["waba_id"] is None and row["phone_number_id"] == ""
    assert row["provider_profile_id"] is None and row["external_account_id"] == ""
    assert row["has_access_token"] is False and row["has_app_secret"] is False
    assert row["webhook_url"] == panel["webhook_url"]  # the screen draws it
    for secret in (verify_token, "waba-654321", "pn-123456", "prof-secret-profile", "acct-77"):
        assert secret not in numbers.text
    saved = api.put(f"{base}/whatsapp-cloud/channels/{world['cloud_mine']['id']}", json={"agent_id": world["agent"]["id"], "label": "X"})
    assert saved.json()["webhook_verify_token"] == "" and verify_token not in saved.text

    lines = api.get(f"{base}/whatsapp/clients/{mine['id']}/channels")
    assert lines.json()[0]["has_session"] is True
    assert "SESSION-STATE-SECRET" not in lines.text and "encrypted" not in lines.text

    for provider in ("instagram", "messenger"):
        accounts = api.get(f"{base}/social/{provider}/clients/{mine['id']}/channels")
        assert accounts.json()[0]["app_id"] is None and accounts.json()[0]["granted_scopes"] == []
        assert accounts.json()[0]["has_access_token"] is False and accounts.json()[0]["webhook_verify_token"] is None
        assert "provider_profile_id" not in accounts.text
    config = api.get(f"{base}/social/config").json()
    assert all(value["webhook_url"] == "" for value in config.values())
    assert api.get("/api/social/config").json()["instagram"]["webhook_url"]  # the agency keeps it

    # The AI keys stay where they were: none of these answers can carry one.
    for path in (f"/clients/{mine['id']}", f"/webchat/channels/{mine['id']}"):
        assert "sk-or-agency-secret" not in api.get(base + path).text


def test_the_panel_keeps_working_for_the_agency(world):
    api = world["api"]
    api.cookies.delete("portal_access_token")
    other = world["other"]
    assert len(api.get(f"/api/whatsapp/clients/{other['id']}/channels").json()) == 1
    assert api.get(f"/api/whatsapp-cloud/clients/{other['id']}/channels").json()[0]["webhook_verify_token"]
    assert api.get(f"/api/webchat/channels/{other['id']}").status_code == 200
    assert len(api.get(f"/api/social/instagram/clients/{other['id']}/channels").json()) == 1
    assert api.get(f"/api/clients/{other['id']}").status_code == 200
    made = api.post(f"/api/whatsapp/clients/{other['id']}/channels", json={"agent_id": world["foreign_agent"]["id"]})
    assert made.status_code == 201
    # The agency reaches any client's line; a channel of one client is never bound to another's agent.
    assert api.put(f"/api/whatsapp/channels/{made.json()['id']}", json={"agent_id": world["agent"]["id"]}).status_code == 400
