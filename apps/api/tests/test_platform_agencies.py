"""The platform owner manages agencies: create, read, patch, resolve by
address, and see each agency's clients — all without any session of the
agency itself."""

import uuid

from fastapi.testclient import TestClient

from app.models import Client, User
from conftest import TestingSession
from test_platform_auth import _login, _platform_admin


def _admin(client: TestClient) -> None:
    _platform_admin()
    _login(client)


def _create(client: TestClient, name="Agencia Norte", slug=None, email="owner@norte.example.com") -> dict:
    payload = {"name": name, "admin_name": "Owner Norte", "admin_email": email}
    if slug is not None:
        payload["slug"] = slug
    response = client.post("/api/platform/agencies", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_agency_issues_the_first_invitation(client):
    _admin(client)
    body = _create(client)
    assert body["agency"]["slug"] == "agencia-norte"
    assert body["agency"]["client_count"] == 0
    invitation = body["invitation"]
    assert invitation["email"] == "owner@norte.example.com"
    assert invitation["status"] == "pending"
    assert invitation["token"].startswith("inv_")
    assert invitation["url"].endswith(f"/join/{invitation['token']}")
    listed = client.get("/api/platform/agencies").json()
    assert body["agency"]["id"] in {item["id"] for item in listed}


def test_create_with_an_explicit_slug_and_a_collision(client):
    _admin(client)
    assert _create(client, slug="norte")["agency"]["slug"] == "norte"
    response = client.post("/api/platform/agencies", json={
        "name": "Norte dos", "slug": "norte", "admin_name": "B", "admin_email": "b@norte.example.com"})
    assert response.status_code == 409


def test_by_slug_resolves_slug_uuid_and_retired_slug(client):
    _admin(client)
    agency_id = _create(client, name="Agencia Vieja")["agency"]["id"]
    assert client.get("/api/platform/agencies/by-slug/agencia-vieja").status_code == 200
    assert client.get(f"/api/platform/agencies/by-slug/{agency_id}").status_code == 200
    renamed = client.patch(f"/api/platform/agencies/{agency_id}", json={"slug": "agencia-nueva"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["slug"] == "agencia-nueva"
    assert client.get("/api/platform/agencies/by-slug/agencia-vieja").json()["id"] == agency_id
    assert client.get("/api/platform/agencies/by-slug/agencia-nueva").json()["id"] == agency_id
    assert client.get("/api/platform/agencies/by-slug/no-existe").status_code == 404


def test_a_retired_slug_cannot_be_taken_by_a_new_agency(client):
    _admin(client)
    agency_id = _create(client, name="Agencia Uno")["agency"]["id"]
    assert client.patch(f"/api/platform/agencies/{agency_id}", json={"slug": "agencia-dos"}).status_code == 200
    again = client.post("/api/platform/agencies", json={
        "name": "Otra", "slug": "agencia-uno", "admin_name": "C", "admin_email": "c@otra.example.com"})
    assert again.status_code == 409


def test_patch_validates_the_color_and_updates_fields(client):
    _admin(client)
    agency_id = _create(client)["agency"]["id"]
    assert client.patch(f"/api/platform/agencies/{agency_id}", json={"brand_color": "rojo"}).status_code == 400
    updated = client.patch(f"/api/platform/agencies/{agency_id}", json={"name": "Agencia Renombrada", "brand_color": "#123456"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Agencia Renombrada"
    assert updated.json()["brand_color"] == "#123456"


def test_agency_clients_are_confined_to_their_agency(client):
    _admin(client)
    a = _create(client, name="Agencia A", slug="agencia-a", email="a@a.example.com")["agency"]
    b = _create(client, name="Agencia B", slug="agencia-b", email="b@b.example.com")["agency"]
    with TestingSession() as db:
        customer = Client(agency_id=uuid.UUID(a["id"]), name="Cliente de A", portal_slug="cliente-de-a")
        db.add(customer)
        db.commit()
        customer_id = str(customer.id)
    clients = client.get(f"/api/platform/agencies/{a['id']}/clients").json()
    assert any(item["portal_slug"] == "cliente-de-a" for item in clients)
    assert client.get(f"/api/platform/agencies/{b['id']}/clients").json() == []
    assert client.get(f"/api/platform/agencies/{b['id']}/clients/{customer_id}").status_code == 404
    detail = client.get(f"/api/platform/agencies/{a['id']}/clients/{customer_id}")
    assert detail.status_code == 200 and detail.json()["portal_slug"] == "cliente-de-a"


def test_the_list_searches_and_paginates(client):
    _admin(client)
    _create(client, name="Agencia Alfa", slug="alfa", email="a@alfa.example.com")
    _create(client, name="Agencia Beta", slug="beta", email="b@beta.example.com")
    found = client.get("/api/platform/agencies", params={"q": "alfa"}).json()
    assert [item["slug"] for item in found] == ["alfa"]
    page = client.get("/api/platform/agencies", params={"page": 1, "limit": 1})
    assert page.headers["X-Total-Count"] == "2"
    assert len(page.json()) == 1


def test_platform_routes_refuse_every_other_identity(client, authenticated_client):
    _platform_admin()
    assert client.get("/api/platform/agencies").status_code == 401
    assert authenticated_client.get("/api/platform/agencies").status_code == 401
    assert authenticated_client.get("/api/platform/agencies", headers={"Authorization": "Bearer ol_nonsense"}).status_code == 401


def _add_person(agency_id: str, email: str, name: str, role: str) -> None:
    with TestingSession() as db:
        db.add(User(agency_id=uuid.UUID(agency_id), name=name, email=email, password_hash="x", role=role))
        db.commit()


def test_the_agency_shows_who_is_in_it(client):
    """Counts of clients and agents read the same whether an agency is in daily
    use or was opened once and left; the people are what tell them apart."""
    _admin(client)
    agency = _create(client, name="Agencia Norte", email="owner@norte.example.com")["agency"]
    # An invitation is not a person: whoever has not accepted it cannot sign in
    # and is not in the account yet.
    _add_person(agency["id"], "agente@norte.example.com", "Ana Agente", "agent")
    _add_person(agency["id"], "owner@norte.example.com", "Vicente Owner", "admin")

    people = client.get(f"/api/platform/agencies/{agency['id']}/users")
    assert people.status_code == 200, people.text
    body = people.json()
    # Who runs the account comes first, whoever joined last.
    assert [(item["name"], item["role"]) for item in body] == [
        ("Vicente Owner", "admin"),
        ("Ana Agente", "agent"),
    ]
    assert {item["email"] for item in body} == {"owner@norte.example.com", "agente@norte.example.com"}
    # Nothing of the credential itself travels with the person.
    assert all("password_hash" not in item for item in body)
    assert all(item["created_at"] for item in body)


def test_people_are_never_mixed_between_agencies(client):
    _admin(client)
    a = _create(client, name="Agencia A", slug="ag-a", email="a@a.example.com")["agency"]
    b = _create(client, name="Agencia B", slug="ag-b", email="b@b.example.com")["agency"]
    _add_person(a["id"], "de-a@a.example.com", "Persona A", "admin")
    _add_person(b["id"], "de-b@b.example.com", "Persona B", "agent")

    assert [item["email"] for item in client.get(f"/api/platform/agencies/{a['id']}/users").json()] == ["de-a@a.example.com"]
    assert [item["email"] for item in client.get(f"/api/platform/agencies/{b['id']}/users").json()] == ["de-b@b.example.com"]


def test_an_agency_with_nobody_yet_answers_with_an_empty_list(client):
    """The invitation the platform sends is still open: nobody is inside yet, and
    the profile has to say so rather than fail or invent someone."""
    _admin(client)
    agency = _create(client, name="Agencia Vacia", email="vacia@vacia.example.com")["agency"]
    assert client.get(f"/api/platform/agencies/{agency['id']}/users").json() == []


def test_people_are_refused_to_every_other_identity(client, authenticated_client):
    _platform_admin()
    agency_id = str(uuid.uuid4())
    assert client.get(f"/api/platform/agencies/{agency_id}/users").status_code == 401
    assert authenticated_client.get(f"/api/platform/agencies/{agency_id}/users").status_code == 401
    assert client.get(f"/api/platform/agencies/{agency_id}/users").status_code == 401


def test_people_of_an_agency_that_is_not_there(client):
    _admin(client)
    assert client.get(f"/api/platform/agencies/{uuid.uuid4()}/users").status_code == 404
