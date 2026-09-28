"""Resolving an agency-panel client by its slug.

Page URLs use the slug (`/clients/{slug}`); the API keeps UUIDs everywhere
else, so this is the only slug-keyed route: it turns an address into the
client the page then works with by id.
"""

from fastapi.testclient import TestClient


def test_a_client_resolves_by_slug(authenticated_client: TestClient):
    client = authenticated_client
    customer = client.post("/api/clients", json={"name": "Dental Marbella", "is_active": True}).json()
    assert customer["portal_slug"] == "dental-marbella"

    resolved = client.get(f"/api/clients/by-slug/{customer['portal_slug']}")
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["id"] == customer["id"]


def test_an_unknown_slug_is_404(authenticated_client: TestClient):
    assert authenticated_client.get("/api/clients/by-slug/no-such-client").status_code == 404
