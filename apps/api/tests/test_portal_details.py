"""Portal Details: a client's own admins edit the client's identity and logo.

Behind the ``details`` portal function (off by default) and the ``client.manage``
permission (admins only). Only name, industry, business type, custom business
description, time zone and the logo can change through it; activation, the
portal's own settings, the custom domain and the agency link never can.
"""

import uuid

from fastapi.testclient import TestClient

from app.models import Client
from conftest import TestingSession

NO_FEATURE = {"detail": "This feature is not enabled for this portal"}
FORBIDDEN = {"detail": "Your role cannot do this"}
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
SHAPE = {
    "name",
    "industry",
    "business_type",
    "business_custom",
    "timezone",
    "owner_name",
    "currency",
    "logo_url",
    "address",
    "google_maps_url",
    "business_hours",
}


def _portal(client: TestClient, on: bool = True, **fields) -> tuple[dict, str]:
    """A client with an open portal, its admin signed in, and Details switched on."""
    customer = client.post("/api/clients", json={"name": "Details Co", "is_active": True, **fields}).json()
    slug = customer["portal_slug"]
    body = {"name": "Ana", "email": f"ana@{slug}.com", "password": "secure-portal"}
    assert client.post(f"/api/clients/{customer['id']}/portal-users", json=body).status_code == 201
    assert client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_enabled": True}).status_code == 200
    if on:
        assert client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_features": {"details": True}}).status_code == 200
    assert client.post(f"/api/portal/{slug}/login", json={"email": body["email"], "password": "secure-portal"}).status_code == 200
    return customer, f"/api/portal/{slug}/client"


def _stored(customer: dict) -> Client:
    with TestingSession() as db:
        row = db.get(Client, uuid.UUID(customer["id"]))
        row.logo_data  # a deferred column: load it before the row leaves the session
        db.expunge(row)
        return row


def test_get_returns_the_details_and_nothing_agency_side(authenticated_client: TestClient):
    client = authenticated_client
    customer, url = _portal(client, industry="health_wellness", business_type="dental", timezone="America/Bogota")
    response = client.get(url)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == SHAPE
    assert (body["name"], body["industry"], body["business_type"], body["timezone"]) == ("Details Co", "health_wellness", "dental", "America/Bogota")
    assert body["business_custom"] == "" and body["logo_url"] is None


def test_patch_changes_the_allowed_fields_and_returns_the_same_object(authenticated_client: TestClient):
    client = authenticated_client
    customer, url = _portal(client)
    response = client.patch(
        url,
        json={"name": "  New Name", "industry": "health_wellness", "business_type": "dental", "business_custom": "Family clinic", "timezone": "Europe/Madrid"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == client.get(url).json()
    assert response.json()["business_type"] == "dental" and response.json()["timezone"] == "Europe/Madrid"
    assert response.json()["business_custom"] == "Family clinic"
    stored = _stored(customer)
    assert (stored.industry, stored.business_type, stored.timezone) == ("health_wellness", "dental", "Europe/Madrid")
    # The agency sees the change too: one row, two doors.
    assert client.get(f"/api/clients/{customer['id']}").json()["business_custom"] == "Family clinic"

    # A partial patch leaves the rest alone; an empty one changes nothing.
    assert client.patch(url, json={"name": "Renamed"}).json()["timezone"] == "Europe/Madrid"
    assert client.patch(url, json={}).json()["name"] == "Renamed"
    # An explicit null is not a value for these columns, so it is ignored.
    assert client.patch(url, json={"name": None, "timezone": None}).json()["name"] == "Renamed"
    # Changing the industry drops a business type that no longer belongs to it.
    switched = client.patch(url, json={"industry": "real_estate"})
    assert switched.status_code == 200 and switched.json()["business_type"] == ""


def test_patch_never_touches_agency_side_fields(authenticated_client: TestClient):
    client = authenticated_client
    customer, url = _portal(client)
    before = client.get(f"/api/clients/{customer['id']}").json()
    response = client.patch(
        url,
        json={
            "name": "Only this changes",
            "is_active": False,
            "portal_enabled": False,
            "portal_slug": "hijacked",
            "portal_title": "Hijacked",
            "portal_features": {"api": True, "agents": True},
            "portal_domain": "evil.example.com",
            "portal_domain_verified": True,
            "agency_id": "00000000-0000-0000-0000-000000000000",
            "id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert response.status_code == 200, response.text
    assert set(response.json()) == SHAPE
    after = client.get(f"/api/clients/{customer['id']}").json()
    assert after["name"] == "Only this changes"
    changed = {key for key in before if before[key] != after[key]}
    assert changed == {"name", "updated_at"}, changed
    stored = _stored(customer)
    assert stored.is_active is True and stored.portal_enabled is True and stored.portal_slug == customer["portal_slug"]
    assert stored.portal_domain is None and str(stored.agency_id) != "00000000-0000-0000-0000-000000000000"
    # The portal still answers on its slug, so the session did not break either.
    assert client.get(url).status_code == 200


def test_invalid_values_are_refused_and_store_nothing(authenticated_client: TestClient):
    client = authenticated_client
    customer, url = _portal(client, industry="health_wellness", business_type="dental")
    bad = [
        {"timezone": "Mars/Olympus"},
        {"industry": "not-an-industry"},
        {"industry": "health_wellness", "business_type": "not-a-type"},
        {"industry": "", "business_type": "dental"},  # a type needs its industry
        {"name": ""},
        {"name": "x" * 181},
        {"business_custom": "x" * 121},
        {"timezone": "x" * 65},
    ]
    for body in bad:
        response = client.patch(url, json={"name": "Half applied", **body} if "name" not in body else body)
        assert response.status_code == 422, (body, response.status_code, response.text)
    stored = _stored(customer)
    assert (stored.name, stored.industry, stored.business_type, stored.timezone) == ("Details Co", "health_wellness", "dental", "UTC")


def test_the_logo_can_be_uploaded_replaced_and_removed(authenticated_client: TestClient):
    client = authenticated_client
    customer, url = _portal(client)
    slug = customer["portal_slug"]

    uploaded = client.post(f"{url}/logo", files={"file": ("logo.png", PNG, "image/png")})
    assert uploaded.status_code == 200, uploaded.text
    assert set(uploaded.json()) == SHAPE
    logo_url = uploaded.json()["logo_url"]
    assert logo_url.startswith(f"/api/portal/{slug}/client-logo")
    assert client.get(url).json()["logo_url"] == logo_url
    # The URL the portal draws the logo with serves the stored bytes.
    served = client.get(logo_url)
    assert served.status_code == 200 and served.content == PNG
    assert served.headers["content-type"].startswith("image/png")
    # The agency's view of the same client has it too.
    assert client.get(f"/api/clients/{customer['id']}").json()["logo_url"]

    replaced = client.post(f"{url}/logo", files={"file": ("logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml")})
    assert replaced.status_code == 200
    assert client.get(logo_url.split("?")[0]).headers["content-type"].startswith("image/svg+xml")

    removed = client.delete(f"{url}/logo")
    assert removed.status_code == 200 and removed.json()["logo_url"] is None
    assert client.get(url).json()["logo_url"] is None
    assert client.get(f"/api/portal/{slug}/client-logo").status_code == 404
    assert client.delete(f"{url}/logo").status_code == 200  # removing nothing is fine
    assert _stored(customer).logo_data is None


def test_the_logo_is_validated_like_the_agencys(authenticated_client: TestClient):
    client = authenticated_client
    customer, url = _portal(client)
    wrong_type = client.post(f"{url}/logo", files={"file": ("logo.gif", b"GIF89a", "image/gif")})
    assert wrong_type.status_code == 400
    too_big = client.post(f"{url}/logo", files={"file": ("logo.png", b"0" * (2 * 1024 * 1024 + 1), "image/png")})
    assert too_big.status_code == 413
    exactly = client.post(f"{url}/logo", files={"file": ("logo.png", b"0" * (2 * 1024 * 1024), "image/png")})
    assert exactly.status_code == 200
    assert client.post(f"{url}/logo").status_code == 422  # no file
    for kind in ("image/jpeg", "image/webp"):
        assert client.post(f"{url}/logo", files={"file": ("logo", PNG, kind)}).status_code == 200


def test_the_function_is_off_by_default_and_closes_every_route(authenticated_client: TestClient):
    client = authenticated_client
    customer, url = _portal(client, on=False)
    assert customer["portal_features"]["details"] is False
    calls = [
        client.get(url),
        client.patch(url, json={"name": "Nope"}),
        client.post(f"{url}/logo", files={"file": ("logo.png", PNG, "image/png")}),
        client.delete(f"{url}/logo"),
        client.get(f"/api/portal/{customer['portal_slug']}/industries"),
    ]
    for response in calls:
        assert response.status_code == 403 and response.json() == NO_FEATURE
    assert _stored(customer).name == "Details Co" and _stored(customer).logo_data is None
    assert "details" not in client.get(f"/api/portal/{customer['portal_slug']}/me").json()["features"]

    client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_features": {"details": True}})
    assert client.get(url).status_code == 200
    assert "details" in client.get(f"/api/portal/{customer['portal_slug']}/me").json()["features"]


def test_an_agent_cannot_read_or_change_the_details(authenticated_client: TestClient):
    client = authenticated_client
    customer, url = _portal(client)
    body = {"name": "Beto", "email": "beto@details.co", "password": "secure-portal", "role": "agent"}
    assert client.post(f"/api/clients/{customer['id']}/portal-users", json=body).status_code == 201
    agent = TestClient(client.app)
    assert agent.post(f"/api/portal/{customer['portal_slug']}/login", json={"email": body["email"], "password": "secure-portal"}).status_code == 200
    assert agent.get(url).json() == FORBIDDEN
    assert agent.patch(url, json={"name": "Nope"}).json() == FORBIDDEN
    assert agent.post(f"{url}/logo", files={"file": ("logo.png", PNG, "image/png")}).json() == FORBIDDEN
    assert agent.delete(f"{url}/logo").json() == FORBIDDEN
    assert agent.get(f"/api/portal/{customer['portal_slug']}/industries").json() == FORBIDDEN
    assert _stored(customer).name == "Details Co"
    # With the function off, the feature refusal comes first, for the agent as for the admin.
    client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_features": {"details": False}})
    assert agent.get(url).json() == NO_FEATURE


def test_a_portal_only_reaches_its_own_client(authenticated_client: TestClient):
    client = authenticated_client
    other = client.post("/api/clients", json={"name": "Other Co", "is_active": True}).json()
    customer, url = _portal(client)
    assert client.patch(url, json={"name": "Renamed"}).json()["name"] == "Renamed"
    assert _stored(other).name == "Other Co"
    # The client is resolved from the session, never from the request.
    assert client.patch(url, json={"id": other["id"], "name": "Still mine"}).json()["name"] == "Still mine"
    assert _stored(other).name == "Other Co"
    bare = TestClient(client.app)
    assert bare.get(url).status_code == 401
    assert bare.patch(url, json={"name": "Nope"}).status_code == 401


def test_the_industry_catalog_is_the_agencys_own(authenticated_client: TestClient):
    client = authenticated_client
    customer, _ = _portal(client)
    portal = client.get(f"/api/portal/{customer['portal_slug']}/industries")
    assert portal.status_code == 200, portal.text
    assert portal.json() == client.get("/api/industries").json()
    assert portal.json()  # not empty
    assert TestClient(client.app).get(f"/api/portal/{customer['portal_slug']}/industries").status_code == 401


def test_the_session_lists_the_details_function_and_permission(authenticated_client: TestClient):
    client = authenticated_client
    customer, _ = _portal(client)
    me = client.get(f"/api/portal/{customer['portal_slug']}/me").json()
    assert "details" in me["features"] and "client.manage" in me["permissions"]
