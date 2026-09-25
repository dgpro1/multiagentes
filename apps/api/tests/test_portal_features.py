"""Per-client portal features: stored on the client, exposed in the session, enforced on the API.

The agency decides, client by client, which functions exist inside a portal.
The switches are stored as a JSON object on the client and read through
app.portal_features.normalize, so a missing key means its default. These tests
hold the catalog, the agency's PATCH, the session lists and, above all, the
server-side refusal: hiding a screen is a courtesy, the 403 is the rule.
"""

import pathlib
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app import portal_features
from app.models import Client
from conftest import TestingSession, customer_conversation, login_legacy_owner

NO_FEATURE = {"detail": "This feature is not enabled for this portal"}
ALWAYS_ON_TODAY = ["inbox", "contacts", "pipeline", "calendar", "reports", "teams", "tags", "templates", "canned"]
NOT_BUILT_YET = [
    "agents", "api", "channels.whatsapp", "channels.whatsapp_cloud", "channels.instagram", "channels.messenger",
    "channels.webchat", "details", "professionals", "services", "appointments",
]
ZERO = "00000000-0000-0000-0000-000000000000"


def _business(client: TestClient, name: str = "Features Co") -> dict:
    return client.post("/api/clients", json={"name": name, "is_active": True}).json()


def _portal(client: TestClient, name: str = "Features Co") -> tuple[dict, str, dict]:
    """A client with an open portal and its first person (an admin) signed in.

    Returns (client, base url of the portal API, the login session).
    """
    customer = _business(client, name)
    slug = customer["portal_slug"]
    payload = {"name": "Ana", "email": f"ana@{slug}.com", "password": "secure-portal"}
    assert client.post(f"/api/clients/{customer['id']}/portal-users", json=payload).status_code == 201
    assert client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_enabled": True}).status_code == 200
    session = client.post(f"/api/portal/{slug}/login", json={"email": payload["email"], "password": "secure-portal"})
    assert session.status_code == 200, session.text
    return customer, f"/api/portal/{slug}", session.json()


def _set(client: TestClient, customer: dict, **switches: bool) -> dict:
    response = client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_features": switches})
    assert response.status_code == 200, response.text
    return response.json()


def _stored(customer: dict) -> dict:
    with TestingSession() as db:
        return dict(db.get(Client, uuid.UUID(customer["id"])).portal_features)


# --- the catalog -----------------------------------------------------------------


def test_the_catalog_defaults_keep_every_existing_portal_as_it_is():
    assert [key for key, on in portal_features.CATALOG if on] == ALWAYS_ON_TODAY
    assert [key for key, on in portal_features.CATALOG if not on] == NOT_BUILT_YET
    assert len(portal_features.KEYS) == len(set(portal_features.KEYS))


def test_normalize_fills_defaults_drops_unknown_keys_and_coerces_to_bool():
    assert portal_features.normalize(None) == portal_features.DEFAULTS
    assert portal_features.normalize({}) == portal_features.DEFAULTS
    result = portal_features.normalize({"calendar": False, "agents": 1, "reports": 0, "nonsense": True, "inbox": "yes"})
    assert set(result) == set(portal_features.KEYS)
    assert result["calendar"] is False and result["agents"] is True and result["reports"] is False and result["inbox"] is True
    assert "nonsense" not in result
    assert portal_features.normalize("not a dict") == portal_features.DEFAULTS  # type: ignore[arg-type]


def test_normalize_never_hands_out_the_shared_defaults():
    portal_features.normalize(None)["inbox"] = False
    portal_features.defaults()["inbox"] = False
    assert portal_features.DEFAULTS["inbox"] is True


def test_validate_patch_refuses_unknown_keys_and_non_booleans():
    from fastapi import HTTPException

    assert portal_features.validate_patch({"calendar": False}) == {"calendar": False}
    with pytest.raises(HTTPException) as unknown:
        portal_features.validate_patch({"telepathy": True})
    assert unknown.value.status_code == 422 and "telepathy" in unknown.value.detail
    with pytest.raises(HTTPException) as bad:
        portal_features.validate_patch({"calendar": "no"})
    assert bad.value.status_code == 422 and "calendar" in bad.value.detail


def test_enabled_keys_follow_the_catalog_order():
    class Fake:
        portal_features = {"contacts": False, "agents": True, "junk": True}

    assert portal_features.enabled_keys(Fake()) == [k for k in ALWAYS_ON_TODAY if k != "contacts"] + ["agents"]


def test_the_web_mirror_lists_the_same_keys_and_defaults():
    mirror = pathlib.Path(__file__).resolve().parents[2] / "web" / "lib" / "portal-features.ts"
    if not mirror.exists():
        pytest.skip("apps/web/lib/portal-features.ts does not exist yet, so there is nothing to compare")
    listed = re.findall(r'\{\s*key:\s*"([^"]+)",\s*default:\s*(true|false)\s*\}', mirror.read_text(encoding="utf-8"))
    assert listed, "could not find the PORTAL_FEATURES list in portal-features.ts"
    assert [(key, on == "true") for key, on in listed] == list(portal_features.CATALOG)


# --- storage and the agency's API ------------------------------------------------


def test_a_new_client_has_the_defaults_and_the_agency_sees_the_full_set(authenticated_client: TestClient):
    client = authenticated_client
    customer = _business(client)
    assert customer["portal_features"] == portal_features.DEFAULTS
    assert _stored(customer) == portal_features.DEFAULTS
    assert client.get(f"/api/clients/{customer['id']}").json()["portal_features"] == portal_features.DEFAULTS
    assert client.get("/api/clients").json()[0]["portal_features"] == portal_features.DEFAULTS


def test_unknown_keys_and_missing_keys_in_storage_are_read_as_defaults(authenticated_client: TestClient):
    client = authenticated_client
    customer = _business(client)
    with TestingSession() as db:
        db.get(Client, uuid.UUID(customer["id"])).portal_features = {"calendar": False, "old_screen": True}
        db.commit()
    seen = client.get(f"/api/clients/{customer['id']}").json()["portal_features"]
    assert seen == {**portal_features.DEFAULTS, "calendar": False}
    assert "old_screen" not in seen
    # An empty object is all defaults.
    with TestingSession() as db:
        db.get(Client, uuid.UUID(customer["id"])).portal_features = {}
        db.commit()
    assert client.get(f"/api/clients/{customer['id']}").json()["portal_features"] == portal_features.DEFAULTS


def test_the_database_itself_fills_the_defaults_for_rows_the_application_did_not_write(authenticated_client: TestClient):
    """What the migration relies on: the server default carries the full set."""
    from sqlalchemy import text

    customer = _business(authenticated_client)
    with TestingSession() as db:
        db.execute(text("UPDATE clients SET portal_features = DEFAULT WHERE id = :id"), {"id": customer["id"]})
        db.commit()
    assert _stored(customer) == portal_features.DEFAULTS


def test_every_key_the_migration_writes_has_the_catalog_default():
    import json

    source = (pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0059_portal_features.py").read_text(encoding="utf-8")
    namespace: dict = {}
    # Only the DEFAULTS constant: the migration must not import the live catalog.
    match = re.search(r"^DEFAULTS = \((.*?)\n\)\n", source, re.S | re.M)
    assert match, "0059 must spell its defaults out"
    exec("DEFAULTS = (" + match.group(1) + ")", namespace)
    written = json.loads(namespace["DEFAULTS"])
    # 0059 is never edited, so functions added later are not in it: they are
    # read as their catalog default when a row does not store them. What it does
    # write must agree with the catalog.
    assert written and set(written) <= set(portal_features.DEFAULTS)
    assert written == {key: portal_features.DEFAULTS[key] for key in written}
    assert not re.search(r"^\s*(from|import)\s+app", source, re.M)


def test_patching_one_switch_leaves_every_other_untouched(authenticated_client: TestClient):
    client = authenticated_client
    customer = _business(client)
    after = _set(client, customer, calendar=False)
    assert after["portal_features"] == {**portal_features.DEFAULTS, "calendar": False}
    after = _set(client, customer, agents=True)
    assert after["portal_features"] == {**portal_features.DEFAULTS, "calendar": False, "agents": True}
    after = _set(client, customer, calendar=True)
    assert after["portal_features"] == {**portal_features.DEFAULTS, "agents": True}
    assert _stored(customer) == {**portal_features.DEFAULTS, "agents": True}
    # An empty patch changes nothing.
    assert _set(client, customer)["portal_features"] == {**portal_features.DEFAULTS, "agents": True}


def test_the_agency_cannot_send_an_unknown_key_or_a_non_boolean(authenticated_client: TestClient):
    client = authenticated_client
    customer = _business(client)
    url = f"/api/clients/{customer['id']}/portal"
    unknown = client.patch(url, json={"portal_features": {"telepathy": True}})
    assert unknown.status_code == 422 and "telepathy" in str(unknown.json())
    for bad in ("false", 0, 1, None, "no", [], {}):
        response = client.patch(url, json={"portal_features": {"calendar": bad}})
        assert response.status_code == 422, (bad, response.text)
    assert client.patch(url, json={"portal_features": ["calendar"]}).status_code == 422
    # A refused patch stores nothing, not even the valid fields sent beside it.
    mixed = client.patch(url, json={"portal_title": "Nope", "portal_features": {"calendar": False, "telepathy": True}})
    assert mixed.status_code == 422
    assert _stored(customer) == portal_features.DEFAULTS
    assert client.get(f"/api/clients/{customer['id']}").json()["portal_title"] == ""


def test_the_switches_travel_with_the_other_portal_fields_without_disturbing_them(authenticated_client: TestClient):
    client = authenticated_client
    customer = _business(client)
    client.post(
        f"/api/clients/{customer['id']}/portal-users",
        json={"name": "Ana", "email": "ana@features.co", "password": "secure-portal"},
    )
    changed = client.patch(
        f"/api/clients/{customer['id']}/portal",
        json={"portal_enabled": True, "portal_slug": "Mi Portal", "portal_title": "Hola", "portal_features": {"reports": False}},
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert (body["portal_enabled"], body["portal_slug"], body["portal_title"]) == (True, "mi-portal", "Hola")
    assert body["portal_features"]["reports"] is False and body["portal_features"]["inbox"] is True
    # A change that omits the switches keeps them.
    kept = client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_title": "Otro"}).json()
    assert kept["portal_title"] == "Otro" and kept["portal_features"]["reports"] is False
    # The existing rule still applies next to a valid switch: nobody to sign in, no portal.
    lonely = _business(client, "Lonely Co")
    refused = client.patch(
        f"/api/clients/{lonely['id']}/portal", json={"portal_enabled": True, "portal_features": {"reports": False}}
    )
    assert refused.status_code == 400
    assert client.get(f"/api/clients/{lonely['id']}").json()["portal_features"] == portal_features.DEFAULTS
    # And the slug rule.
    other = _business(client, "Other Co")
    taken = client.patch(f"/api/clients/{other['id']}/portal", json={"portal_slug": "mi-portal", "portal_features": {"tags": False}})
    assert taken.status_code == 409
    assert client.get(f"/api/clients/{other['id']}").json()["portal_features"] == portal_features.DEFAULTS


def test_the_agency_cannot_change_another_agencys_client(authenticated_client: TestClient):
    client = authenticated_client
    customer = _business(client)
    login_legacy_owner(client)
    url = f"/api/clients/{customer['id']}/portal"
    assert client.patch(url, json={"portal_features": {"calendar": False}}).status_code == 404
    assert client.get(f"/api/clients/{customer['id']}").status_code == 404
    assert _stored(customer) == portal_features.DEFAULTS


# --- the session -----------------------------------------------------------------


def test_login_and_me_list_the_enabled_features(authenticated_client: TestClient):
    client = authenticated_client
    customer, base, session = _portal(client)
    assert session["features"] == ALWAYS_ON_TODAY
    assert client.get(f"{base}/me").json()["features"] == ALWAYS_ON_TODAY

    _set(client, customer, calendar=False, contacts=False, agents=True)
    expected = [k for k in ALWAYS_ON_TODAY if k not in ("calendar", "contacts")] + ["agents"]
    # /me reads the client fresh, so a switch takes effect without signing in again.
    assert client.get(f"{base}/me").json()["features"] == expected
    relogin = client.post(f"{base}/login", json={"email": f"ana@{customer['portal_slug']}.com", "password": "secure-portal"})
    assert relogin.json()["features"] == expected

    _set(client, customer, **{k: False for k in ALWAYS_ON_TODAY})
    assert client.get(f"{base}/me").json()["features"] == ["agents"]


def test_the_mobile_session_lists_the_enabled_features(authenticated_client: TestClient):
    client = authenticated_client
    customer, _, _ = _portal(client)
    credentials = {"email": f"ana@{customer['portal_slug']}.com", "password": "secure-portal"}
    signed_in = client.post("/api/mobile/sign-in", json=credentials)
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["features"] == ALWAYS_ON_TODAY

    _set(client, customer, pipeline=False)
    expected = [k for k in ALWAYS_ON_TODAY if k != "pipeline"]
    again = client.post("/api/mobile/sign-in", json=credentials)
    assert again.json()["features"] == expected
    session = client.get("/api/mobile/session", headers={"Authorization": f"Bearer {again.json()['token']}"})
    assert session.status_code == 200, session.text
    assert session.json()["features"] == expected


# --- enforcement ------------------------------------------------------------------

# (method, path under /api/portal/{slug}, the features that must all be on).
# `{id}` stands for any path parameter.
GATED = [
    ("GET", "/conversations", ["inbox"]),
    ("GET", "/inbox", ["inbox"]),
    ("GET", "/conversations/summary", ["inbox"]),
    ("GET", "/conversations/number/{id}", ["inbox"]),
    ("GET", "/conversations/{id}", ["inbox"]),
    ("GET", "/conversations/{id}/attachments/{id}", ["inbox"]),
    ("POST", "/conversations/{id}/read", ["inbox"]),
    ("PATCH", "/conversations/{id}/mode", ["inbox"]),
    ("PATCH", "/conversations/{id}/team", ["inbox", "teams"]),
    ("POST", "/conversations/{id}/assignment", ["inbox"]),
    ("PATCH", "/conversations/{id}/status", ["inbox"]),
    # Merging two leads into one (tests/test_lead_merge.py); merging also needs contacts.manage.
    ("GET", "/leads/merge-candidates", ["inbox"]),
    ("POST", "/leads/merge", ["inbox"]),
    # The lead card of the inbox and its custom fields (tests/test_lead_card.py).
    ("GET", "/conversations/{id}/lead", ["inbox"]),
    ("PATCH", "/conversations/{id}/lead", ["inbox"]),
    ("GET", "/lead-fields", ["inbox"]),
    ("POST", "/lead-fields", ["inbox"]),
    ("PATCH", "/lead-fields/{id}", ["inbox"]),
    ("DELETE", "/lead-fields/{id}", ["inbox"]),
    ("POST", "/conversations/{id}/reply", ["inbox"]),
    ("POST", "/conversations/{id}/notes", ["inbox"]),
    ("POST", "/conversations/{id}/reply-media", ["inbox"]),
    ("POST", "/conversations/{id}/reply-template", ["inbox", "templates"]),
    ("POST", "/conversations/{id}/messages/{id}/reaction", ["inbox"]),
    ("PATCH", "/conversations/{id}/archive", ["inbox"]),
    ("DELETE", "/conversations/{id}", ["inbox"]),
    ("POST", "/conversations/archive-resolved", ["inbox"]),
    ("POST", "/conversations/delete-archived", ["inbox"]),
    ("GET", "/contacts", ["contacts"]),
    ("POST", "/contacts", ["contacts"]),
    ("GET", "/contacts/import-template", ["contacts"]),
    ("GET", "/contacts/export", ["contacts"]),
    ("POST", "/contacts/import", ["contacts"]),
    ("GET", "/contacts/{id}", ["contacts"]),
    ("PATCH", "/contacts/{id}", ["contacts"]),
    ("POST", "/contacts/{id}/merge", ["contacts"]),
    ("POST", "/contacts/{id}/block", ["contacts"]),
    ("DELETE", "/contacts/{id}", ["contacts"]),
    ("GET", "/contacts/{id}/conversations", ["contacts"]),
    ("POST", "/contacts/{id}/conversations", ["contacts"]),
    ("PUT", "/contacts/{id}/tags", ["tags"]),
    ("GET", "/pipeline/stages", ["pipeline"]),
    ("POST", "/pipeline/stages", ["pipeline"]),
    ("PATCH", "/pipeline/stages/{id}", ["pipeline"]),
    ("POST", "/pipeline/stages/reorder", ["pipeline"]),
    ("DELETE", "/pipeline/stages/{id}", ["pipeline"]),
    ("GET", "/pipeline/board", ["pipeline"]),
    ("POST", "/pipeline/leads", ["pipeline"]),
    ("PATCH", "/conversations/{id}/pipeline", ["pipeline"]),
    ("GET", "/calendar", ["calendar"]),
    ("GET", "/calendar/events", ["calendar"]),
    ("POST", "/calendar/members", ["calendar"]),
    ("PATCH", "/calendar/members/{id}", ["calendar"]),
    ("POST", "/calendar/members/{id}/renew-link", ["calendar"]),
    ("POST", "/calendar/members/{id}/disconnect", ["calendar"]),
    ("DELETE", "/calendar/members/{id}", ["calendar"]),
    ("GET", "/reports", ["reports"]),
    # The client's own details and its professionals (tests/test_portal_details.py, tests/test_professionals.py).
    ("GET", "/client", ["details"]),
    ("PATCH", "/client", ["details"]),
    ("POST", "/client/logo", ["details"]),
    ("DELETE", "/client/logo", ["details"]),
    ("GET", "/industries", ["details"]),
    ("GET", "/professionals", ["professionals"]),
    ("POST", "/professionals", ["professionals"]),
    ("PATCH", "/professionals/{id}", ["professionals"]),
    ("DELETE", "/professionals/{id}", ["professionals"]),
    ("GET", "/services", ["services"]),
    ("POST", "/services", ["services"]),
    ("PATCH", "/services/{id}", ["services"]),
    ("DELETE", "/services/{id}", ["services"]),
    ("GET", "/appointments", ["appointments"]),
    ("POST", "/appointments", ["appointments"]),
    ("PATCH", "/appointments/{id}", ["appointments"]),
    ("DELETE", "/appointments/{id}", ["appointments"]),
    ("GET", "/appointments/availability", ["appointments"]),
    ("POST", "/teams", ["teams"]),
    ("PATCH", "/teams/{id}", ["teams"]),
    ("DELETE", "/teams/{id}", ["teams"]),
    ("POST", "/tags", ["tags"]),
    ("PATCH", "/tags/{id}", ["tags"]),
    ("DELETE", "/tags/{id}", ["tags"]),
    ("POST", "/templates", ["templates"]),
    ("POST", "/templates/samples", ["templates"]),
    ("DELETE", "/templates/{id}", ["templates"]),
    ("POST", "/canned-responses", ["canned"]),
    ("PATCH", "/canned-responses/{id}", ["canned"]),
    ("DELETE", "/canned-responses/{id}", ["canned"]),
    ("GET", "/agents", ["agents"]),
    # The agency's agent screens for the client's admins (tests/test_portal_agents.py).
    ("GET", "/manage/agents", ["agents"]),
    ("POST", "/manage/agents", ["agents"]),
    ("GET", "/manage/agents/{id}", ["agents"]),
    ("PATCH", "/manage/agents/{id}", ["agents"]),
    ("DELETE", "/manage/agents/{id}", ["agents"]),
    ("GET", "/manage/agents/{id}/prompt", ["agents"]),
    ("GET", "/manage/agents/{id}/documents", ["agents"]),
    ("POST", "/manage/agents/{id}/documents", ["agents"]),
    ("POST", "/manage/agents/{id}/documents/reindex", ["agents"]),
    ("POST", "/manage/agents/{id}/documents/{id}/reindex", ["agents"]),
    ("DELETE", "/manage/agents/{id}/documents/{id}", ["agents"]),
    ("GET", "/manage/agents/{id}/qa", ["agents"]),
    ("POST", "/manage/agents/{id}/qa", ["agents"]),
    ("DELETE", "/manage/agents/{id}/qa/{id}", ["agents"]),
    ("GET", "/manage/agents/{id}/escalation-rules", ["agents"]),
    ("PUT", "/manage/agents/{id}/escalation-rules", ["agents"]),
    ("GET", "/manage/agents/{id}/tools", ["agents"]),
    ("POST", "/manage/agents/{id}/tools", ["agents"]),
    ("PATCH", "/manage/agents/{id}/tools/{id}", ["agents"]),
    ("DELETE", "/manage/agents/{id}/tools/{id}", ["agents"]),
    ("POST", "/manage/agents/{id}/tools/test-mcp", ["agents"]),
    ("GET", "/manage/clients", ["agents"]),
    ("GET", "/manage/clients/{id}/teams", ["agents"]),
    ("GET", "/manage/clients/{id}/portal-users", ["agents"]),
    ("GET", "/manage/clients/{id}/contact-tags", ["agents"]),
    ("PATCH", "/manage/clients/{id}/contact-tags/{id}", ["agents", "tags"]),
    ("GET", "/manage/conversations", ["agents"]),
    ("POST", "/manage/conversations", ["agents"]),
    ("GET", "/manage/conversations/{id}", ["agents"]),
    ("DELETE", "/manage/conversations/{id}", ["agents"]),
    ("POST", "/manage/conversations/{id}/messages", ["agents"]),
    ("POST", "/manage/conversations/{id}/media", ["agents"]),
    ("GET", "/manage/conversations/{id}/attachments/{id}", ["agents"]),
    ("GET", "/manage/catalog/available", ["agents"]),
    ("GET", "/manage/catalog/models", ["agents"]),
    ("GET", "/manage/catalog/embedding-models", ["agents"]),
    ("GET", "/manage/catalog/models/{id}", ["agents"]),
    ("GET", "/manage/providers", ["agents"]),
    # The agency's channel screens, one function per channel type (tests/test_portal_channels.py).
    ("GET", "/manage/whatsapp/clients/{id}/channels", ["channels.whatsapp"]),
    ("POST", "/manage/whatsapp/clients/{id}/channels", ["channels.whatsapp"]),
    ("GET", "/manage/whatsapp/channels/{id}", ["channels.whatsapp"]),
    ("PUT", "/manage/whatsapp/channels/{id}", ["channels.whatsapp"]),
    ("DELETE", "/manage/whatsapp/channels/{id}", ["channels.whatsapp"]),
    ("POST", "/manage/whatsapp/channels/{id}/connect", ["channels.whatsapp"]),
    ("POST", "/manage/whatsapp/channels/{id}/disconnect", ["channels.whatsapp"]),
    ("GET", "/manage/whatsapp-cloud/clients/{id}/channels", ["channels.whatsapp_cloud"]),
    ("POST", "/manage/whatsapp-cloud/clients/{id}/channels", ["channels.whatsapp_cloud"]),
    ("PUT", "/manage/whatsapp-cloud/channels/{id}", ["channels.whatsapp_cloud"]),
    ("DELETE", "/manage/whatsapp-cloud/channels/{id}", ["channels.whatsapp_cloud"]),
    ("POST", "/manage/whatsapp-cloud/channels/{id}/connect", ["channels.whatsapp_cloud"]),
    ("POST", "/manage/whatsapp-cloud/channels/{id}/refresh", ["channels.whatsapp_cloud"]),
    ("POST", "/manage/whatsapp-cloud/channels/{id}/disconnect", ["channels.whatsapp_cloud"]),
    ("GET", "/manage/webchat/channels/{id}", ["channels.webchat"]),
    ("PUT", "/manage/webchat/channels/{id}", ["channels.webchat"]),
    # The API tab: the client's own integrations, tokens and webhooks (tests/test_portal_api.py).
    ("GET", "/manage/integrations/scopes", ["api"]),
    ("GET", "/manage/integrations", ["api"]),
    ("POST", "/manage/integrations", ["api"]),
    ("PATCH", "/manage/integrations/{id}", ["api"]),
    ("DELETE", "/manage/integrations/{id}", ["api"]),
    ("GET", "/manage/integrations/{id}/tokens", ["api"]),
    ("POST", "/manage/integrations/{id}/tokens", ["api"]),
    ("DELETE", "/manage/integrations/{id}/tokens/{id}", ["api"]),
    ("GET", "/manage/integrations/{id}/webhooks", ["api"]),
    ("POST", "/manage/integrations/{id}/webhooks", ["api"]),
    ("DELETE", "/manage/integrations/{id}/webhooks/{id}", ["api"]),
    ("GET", "/manage/integrations/{id}/webhooks/{id}/deliveries", ["api"]),
    ("POST", "/manage/integrations/{id}/webhooks/{id}/deliveries/{id}/replay", ["api"]),
]

# Channel routes whose function is not one fixed key: the ``/social/{provider}``
# routes need the provider's own type, the config and the client's own record
# need any one of several. Their refusals are held in tests/test_portal_channels.py.
CHANNEL_SPECIAL = [
    ("GET", "/manage/social/config"),
    ("GET", "/manage/social/{id}/clients/{id}/channels"),
    ("PATCH", "/manage/social/{id}/channels/{id}"),
    ("POST", "/manage/social/{id}/channels/{id}/connect"),
    ("POST", "/manage/social/{id}/channels/{id}/disconnect"),
    ("POST", "/manage/social/{id}/oauth/start"),
    ("GET", "/manage/social/{id}/channels/{id}/import-history"),
    ("POST", "/manage/social/{id}/channels/{id}/import-history"),
    ("GET", "/manage/clients/{id}"),
]

# Reads the portal draws itself with: they answer whatever the switches say.
ALWAYS_OPEN = [
    ("GET", "/me"),
    ("GET", "/members"),
    ("GET", "/channels"),
    ("GET", "/teams"),
    ("GET", "/tags"),
    ("GET", "/templates"),
    ("GET", "/canned-responses"),
]

# Routes with no feature behind them at all: the door and the person's own state.
UNGATED = [
    ("GET", ""),
    ("GET", "/logo"),
    ("GET", "/client-logo"),
    ("POST", "/login"),
    ("POST", "/logout"),
    ("PATCH", "/me"),
]


def _shape(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{id}", path)


def test_every_portal_route_has_a_feature_decision():
    """The matrix is only worth something if it names real routes and leaves none out."""
    from app.main import app

    def _walk(app_or_router, prefix=""):
        for r in app_or_router.routes:
            if type(r).__name__ == "_IncludedRouter":
                p = prefix + (getattr(r.include_context, "prefix", "") or "")
                yield from _walk(r.original_router, p)
            elif hasattr(r, "path"):
                yield prefix + r.path, r

    prefix = "/api/portal/{slug}"
    actual = {
        (method, _shape(path[len(prefix):]))
        for path, route in _walk(app)
        if path.startswith(prefix)
        for method in route.methods
        if method != "HEAD"
    }
    decided = {(m, _shape(p)) for m, p, _ in GATED} | {(m, p) for m, p in ALWAYS_OPEN} | set(UNGATED) | set(CHANNEL_SPECIAL)
    assert not actual - decided, f"portal routes with no feature decision: {sorted(actual - decided)}"
    assert not decided - actual, f"the matrix names routes that do not exist: {sorted(decided - actual)}"


@pytest.mark.parametrize("method,path,features", GATED, ids=[f"{m} {p} [{'+'.join(f)}]" for m, p, f in GATED])
def test_a_gated_route_answers_403_when_any_of_its_features_is_off(authenticated_client: TestClient, method, path, features):
    client = authenticated_client
    customer, base, _ = _portal(client)
    # Everything on (the not-yet-built `agents` too), then each feature off in turn.
    _set(client, customer, **{key: True for key in portal_features.KEYS})
    for feature in features:
        _set(client, customer, **{feature: False})
        response = client.request(method, base + path.replace("{id}", ZERO), json={})
        assert response.status_code == 403, (feature, response.status_code, response.text)
        assert response.json() == NO_FEATURE
        _set(client, customer, **{feature: True})


def test_an_unauthenticated_call_gets_401_not_the_feature_refusal(authenticated_client: TestClient):
    client = authenticated_client
    customer, base, _ = _portal(client)
    _set(client, customer, calendar=False)
    assert TestClient(client.app).get(f"{base}/calendar").status_code == 401


def test_the_lists_the_portal_draws_itself_with_stay_open_when_their_feature_is_off(authenticated_client: TestClient):
    client = authenticated_client
    customer, base, _ = _portal(client)
    _set(client, customer, **{key: False for key in portal_features.KEYS})
    for method, path in ALWAYS_OPEN:
        response = client.request(method, f"{base}{path}")
        # /templates has a business rule of its own (it needs a WhatsApp API number): 409, never the feature 403.
        assert response.status_code == (409 if path == "/templates" else 200), (path, response.status_code, response.text)
        assert response.json() != NO_FEATURE
    assert client.patch(f"{base}/me", json={"availability": "away"}).status_code == 200
    assert client.get(f"{base}/me").json()["features"] == []


def test_with_the_features_on_the_portal_answers_as_before(authenticated_client: TestClient):
    client = authenticated_client
    customer, base, _ = _portal(client)
    for path in ("/conversations", "/inbox", "/conversations/summary", "/contacts", "/pipeline/stages", "/pipeline/board",
                 "/calendar", "/reports", "/teams", "/tags", "/templates", "/canned-responses", "/channels", "/members"):
        response = client.get(f"{base}{path}", params={"from": "2026-01-01", "to": "2026-01-31"})
        assert response.status_code == (409 if path == "/templates" else 200), (path, response.status_code, response.text)
    # `agents` is off by default, so its list is closed until the agency opens it.
    assert client.get(f"{base}/agents").json() == NO_FEATURE
    _set(client, customer, agents=True)
    assert client.get(f"{base}/agents").status_code == 200


def test_turning_one_feature_off_closes_only_that_feature(authenticated_client: TestClient):
    client = authenticated_client
    customer, base, _ = _portal(client)
    _set(client, customer, calendar=False)
    assert client.get(f"{base}/calendar").json() == NO_FEATURE
    assert client.get(f"{base}/calendar/events").json() == NO_FEATURE
    assert client.get(f"{base}/contacts").status_code == 200
    assert client.get(f"{base}/inbox").status_code == 200
    assert client.get(f"{base}/pipeline/board").status_code == 200
    _set(client, customer, calendar=True)
    assert client.get(f"{base}/calendar").status_code == 200


def test_refused_work_leaves_no_trace_and_reopening_restores_it(authenticated_client: TestClient):
    client = authenticated_client
    customer, base, _ = _portal(client)
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post(
        "/api/agents",
        json={"client_id": customer["id"], "provider": "openrouter", "model": "gpt-4.1-mini", "name": "Beto", "is_active": True},
    ).json()
    conversation_id = customer_conversation(client, agent["id"])["id"]

    _set(client, customer, inbox=False, contacts=False, tags=False, canned=False)
    assert client.post(f"{base}/conversations/{conversation_id}/reply", json={"content": "Hola"}).json() == NO_FEATURE
    assert client.get(f"{base}/conversations/{conversation_id}").json() == NO_FEATURE
    assert client.post(f"{base}/contacts", json={"name": "Rita", "phone": "573001112233"}).json() == NO_FEATURE
    assert client.post(f"{base}/tags", json={"name": "VIP"}).json() == NO_FEATURE
    assert client.post(f"{base}/canned-responses", json={"shortcut": "hola", "content": "Hola!"}).json() == NO_FEATURE

    _set(client, customer, inbox=True, contacts=True, tags=True, canned=True)
    assert client.get(f"{base}/contacts").json() == []
    assert client.get(f"{base}/tags").json() == []
    assert client.get(f"{base}/canned-responses").json() == []
    detail = client.get(f"{base}/conversations/{conversation_id}").json()
    assert [m for m in detail["messages"] if m["sender_type"] == "human"] == []


def test_a_role_without_the_permission_still_gets_its_usual_refusal_with_the_feature_on(authenticated_client: TestClient):
    client = authenticated_client
    customer, base, _ = _portal(client)  # the first person is the admin
    client.post(
        f"/api/clients/{customer['id']}/portal-users",
        json={"name": "Beto", "email": "beto@features.co", "password": "secure-portal", "role": "agent"},
    )
    agent = TestClient(client.app)
    assert agent.post(f"{base}/login", json={"email": "beto@features.co", "password": "secure-portal"}).status_code == 200
    forbidden = {"detail": "Your role cannot do this"}
    assert agent.get(f"{base}/reports").json() == forbidden
    assert agent.post(f"{base}/tags", json={"name": "Nuevo"}).json() == forbidden
    assert agent.post(f"{base}/teams", json={"name": "Soporte", "member_ids": []}).json() == forbidden
    assert agent.post(f"{base}/canned-responses", json={"shortcut": "a", "content": "b"}).json() == forbidden
    assert agent.get(f"{base}/contacts/export").json() == forbidden
    assert agent.get(f"{base}/inbox").status_code == 200  # what the role does allow

    # With the feature off the answer is the feature refusal, for the agent and the admin alike.
    _set(client, customer, reports=False, tags=False)
    assert agent.get(f"{base}/reports").json() == NO_FEATURE
    assert client.get(f"{base}/reports").json() == NO_FEATURE
    assert client.post(f"{base}/tags", json={"name": "Nuevo"}).json() == NO_FEATURE


def test_each_portal_has_its_own_switches(authenticated_client: TestClient):
    client = authenticated_client
    first, first_base, _ = _portal(client, "First Co")
    second, second_base, _ = _portal(client, "Second Co")  # signing in here replaces the session cookie
    _set(client, second, calendar=False)
    assert client.get(f"{second_base}/calendar").json() == NO_FEATURE
    relogin = client.post(f"{first_base}/login", json={"email": f"ana@{first['portal_slug']}.com", "password": "secure-portal"})
    assert relogin.status_code == 200
    assert client.get(f"{first_base}/calendar").status_code == 200


# --- what features must not touch --------------------------------------------------


def test_the_agency_panel_and_api_v1_ignore_the_portal_features(authenticated_client: TestClient):
    client = authenticated_client
    customer, _, _ = _portal(client)
    _set(client, customer, **{key: False for key in portal_features.KEYS})
    cid = customer["id"]

    # The agency's own routes for the same functions still work.
    assert client.get(f"/api/clients/{cid}/pipeline/board").status_code == 200
    assert client.get(f"/api/clients/{cid}/calendar").status_code == 200
    assert client.get(f"/api/clients/{cid}/contact-tags").status_code == 200
    assert client.get(f"/api/clients/{cid}/teams").status_code == 200
    assert client.get("/api/conversations").status_code == 200

    # And so does the public API.
    integration = client.post(
        "/api/integrations",
        json={"name": "n8n", "scopes": ["clients.read", "contacts.read", "inbox.read", "pipeline.read", "calendar.read"]},
    ).json()
    token = client.post(f"/api/integrations/{integration['id']}/tokens", json={"expires_in_days": 30}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    bare = TestClient(client.app)
    one = bare.get(f"/api/v1/clients/{cid}", headers=headers)
    assert one.status_code == 200, one.text
    assert one.json()["portal_features"] == {key: False for key in portal_features.KEYS}
    assert bare.get(f"/api/v1/clients/{cid}/contacts", headers=headers).status_code == 200
    assert bare.get(f"/api/v1/clients/{cid}/pipeline/board", headers=headers).status_code == 200
