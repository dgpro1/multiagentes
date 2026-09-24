"""Professionals: the people a client books time with and the hours they work.

Managed from two doors, the agency's client page and the client's own portal
(behind the ``professionals`` portal function, off by default), over the same
rows. These tests hold the schedule rules, the ownership boundary and the
portal's function and permission checks.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.models import Client, Professional
from app.schemas_professionals import DAYS
from conftest import TestingSession, login_legacy_owner

NO_FEATURE = {"detail": "This feature is not enabled for this portal"}
FORBIDDEN = {"detail": "Your role cannot do this"}
ZERO = "00000000-0000-0000-0000-000000000000"
WEEK = {"mon": [["09:00", "13:00"], ["14:00", "18:00"]], "tue": [["09:00", "18:00"]]}


def _customer(client: TestClient, name: str = "Dental Co") -> dict:
    return client.post("/api/clients", json={"name": name, "is_active": True}).json()


def _url(customer: dict) -> str:
    return f"/api/clients/{customer['id']}/professionals"


def _portal(client: TestClient, name: str = "Portal Co", on: bool = True) -> tuple[dict, str]:
    """A client with an open portal, its admin signed in, and the function switched on."""
    customer = _customer(client, name)
    slug = customer["portal_slug"]
    body = {"name": "Ana", "email": f"ana@{slug}.com", "password": "secure-portal"}
    assert client.post(f"/api/clients/{customer['id']}/portal-users", json=body).status_code == 201
    assert client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_enabled": True}).status_code == 200
    if on:
        assert client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_features": {"professionals": True}}).status_code == 200
    assert client.post(f"/api/portal/{slug}/login", json={"email": body["email"], "password": "secure-portal"}).status_code == 200
    return customer, f"/api/portal/{slug}/professionals"


def _agent_session(client: TestClient, customer: dict) -> TestClient:
    """A second person of the same portal with the agent role, in their own session."""
    slug = customer["portal_slug"]
    body = {"name": "Beto", "email": f"beto@{slug}.com", "password": "secure-portal", "role": "agent"}
    assert client.post(f"/api/clients/{customer['id']}/portal-users", json=body).status_code == 201
    other = TestClient(client.app)
    assert other.post(f"/api/portal/{slug}/login", json={"email": body["email"], "password": "secure-portal"}).status_code == 200
    return other


# --- agency ---------------------------------------------------------------------------


def test_the_agency_creates_lists_updates_and_deletes(authenticated_client: TestClient):
    client = authenticated_client
    customer = _customer(client)
    assert client.get(_url(customer)).json() == []

    created = client.post(_url(customer), json={"name": "  Dr. Ruiz  ", "role": "Orthodontist", "weekly_hours": WEEK})
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == {"id", "client_id", "name", "role", "color", "is_active", "slot_minutes", "weekly_hours", "created_at", "updated_at"}
    assert body["client_id"] == customer["id"]
    assert (body["name"], body["role"], body["is_active"], body["slot_minutes"]) == ("Dr. Ruiz", "Orthodontist", True, 30)
    assert body["color"].startswith("#") and len(body["color"]) == 7
    assert body["weekly_hours"]["mon"] == [["09:00", "13:00"], ["14:00", "18:00"]]

    listed = client.get(_url(customer)).json()
    assert [row["id"] for row in listed] == [body["id"]]

    changed = client.patch(
        f"{_url(customer)}/{body['id']}",
        json={"name": "Dra. Ruiz", "is_active": False, "slot_minutes": 45, "color": "#112233", "weekly_hours": {"sat": [["10:00", "12:00"]]}},
    )
    assert changed.status_code == 200, changed.text
    after = changed.json()
    assert (after["name"], after["is_active"], after["slot_minutes"], after["color"]) == ("Dra. Ruiz", False, 45, "#112233")
    # The week is replaced as a whole: Monday and Tuesday are days off now.
    assert after["weekly_hours"]["sat"] == [["10:00", "12:00"]] and after["weekly_hours"]["mon"] == []
    assert after["role"] == "Orthodontist"

    # A patch that leaves the week out keeps it; an explicit null changes nothing either.
    kept = client.patch(f"{_url(customer)}/{body['id']}", json={"role": "Surgeon", "weekly_hours": None, "name": None}).json()
    assert kept["role"] == "Surgeon" and kept["name"] == "Dra. Ruiz" and kept["weekly_hours"]["sat"] == [["10:00", "12:00"]]

    assert client.delete(f"{_url(customer)}/{body['id']}").status_code == 204
    assert client.get(_url(customer)).json() == []
    with TestingSession() as db:
        assert db.get(Professional, uuid.UUID(body["id"])) is None  # a real delete
    assert client.delete(f"{_url(customer)}/{body['id']}").status_code == 404


def test_new_professionals_take_distinct_palette_colors(authenticated_client: TestClient):
    client = authenticated_client
    customer = _customer(client)
    colors = [client.post(_url(customer), json={"name": f"P{i}"}).json()["color"] for i in range(4)]
    assert len(set(colors)) == 4


def test_the_week_always_comes_back_with_all_seven_days(authenticated_client: TestClient):
    client = authenticated_client
    customer = _customer(client)
    empty = client.post(_url(customer), json={"name": "Nadia"}).json()
    assert list(empty["weekly_hours"]) == list(DAYS)
    assert all(ranges == [] for ranges in empty["weekly_hours"].values())
    partial = client.post(_url(customer), json={"name": "Omar", "weekly_hours": {"fri": [["08:00", "12:00"]]}}).json()
    assert list(partial["weekly_hours"]) == list(DAYS) and partial["weekly_hours"]["fri"] == [["08:00", "12:00"]]
    # Ranges are stored sorted, whatever order they were sent in.
    sorted_ = client.post(_url(customer), json={"name": "Pia", "weekly_hours": {"mon": [["14:00", "18:00"], ["09:00", "13:00"]]}}).json()
    assert sorted_["weekly_hours"]["mon"] == [["09:00", "13:00"], ["14:00", "18:00"]]
    # A row written without a week (or with junk) still reads as seven days.
    with TestingSession() as db:
        row = db.get(Professional, uuid.UUID(empty["id"]))
        row.weekly_hours = {}
        db.commit()
    assert list(client.get(_url(customer)).json()[0]["weekly_hours"]) == list(DAYS)


@pytest.mark.parametrize(
    "hours",
    [
        {"mon": [["9:00", "13:00"]]},                      # not zero padded
        {"mon": [["09:00", "25:00"]]},                     # not a time
        {"mon": [["09:00", "24:00"]]},                     # the day ends at 23:59
        {"mon": [["09:60", "13:00"]]},
        {"mon": [["09:00", "9am"]]},
        {"mon": [["13:00", "09:00"]]},                     # ends before it starts
        {"mon": [["09:00", "09:00"]]},                     # empty range
        {"mon": [["09:00", "13:00"], ["12:00", "15:00"]]},  # overlap
        {"mon": [["14:00", "18:00"], ["09:00", "15:00"]]},  # overlap, out of order
        {"funday": [["09:00", "10:00"]]},                  # unknown day
        {"MON": [["09:00", "10:00"]]},                     # days are lower case
        {"mon": [["08:00", "09:00"], ["10:00", "11:00"], ["12:00", "13:00"], ["14:00", "15:00"], ["16:00", "17:00"]]},  # five ranges
        {"mon": [["09:00", "10:00", "11:00"]]},            # not a pair
        {"mon": ["09:00", "10:00"]},                       # not a list of pairs
        {"mon": "09:00-10:00"},                            # not a list
        {"mon": [[9, 10]]},                                # not strings
        ["mon"],                                           # not an object
    ],
)
def test_a_bad_schedule_is_refused_on_create_and_update(authenticated_client: TestClient, hours):
    client = authenticated_client
    customer = _customer(client)
    assert client.post(_url(customer), json={"name": "Bad", "weekly_hours": hours}).status_code == 422
    good = client.post(_url(customer), json={"name": "Good", "weekly_hours": WEEK}).json()
    assert client.patch(f"{_url(customer)}/{good['id']}", json={"weekly_hours": hours}).status_code == 422
    # Nothing was stored, and the good week is intact.
    assert [row["name"] for row in client.get(_url(customer)).json()] == ["Good"]
    assert client.get(_url(customer)).json()[0]["weekly_hours"]["mon"] == WEEK["mon"]


def test_the_edges_of_a_valid_schedule_are_accepted(authenticated_client: TestClient):
    client = authenticated_client
    customer = _customer(client)
    week = {
        "mon": [["00:00", "23:59"]],
        "tue": [["08:00", "09:00"], ["09:00", "10:00"], ["11:00", "12:00"], ["13:00", "14:00"]],  # four, touching
        "wed": [],
    }
    created = client.post(_url(customer), json={"name": "Edge", "weekly_hours": week})
    assert created.status_code == 201, created.text
    assert created.json()["weekly_hours"]["tue"][1] == ["09:00", "10:00"]


@pytest.mark.parametrize("minutes,ok", [(4, False), (5, True), (30, True), (240, True), (241, False), (0, False), (-5, False), ("thirty", False)])
def test_slot_minutes_bounds(authenticated_client: TestClient, minutes, ok):
    client = authenticated_client
    customer = _customer(client)
    created = client.post(_url(customer), json={"name": "Slot", "slot_minutes": minutes})
    assert (created.status_code == 201) is ok, created.text
    row = client.post(_url(customer), json={"name": "Other"}).json()
    changed = client.patch(f"{_url(customer)}/{row['id']}", json={"slot_minutes": minutes})
    assert (changed.status_code == 200) is ok, changed.text


def test_the_name_is_required_and_stripped(authenticated_client: TestClient):
    client = authenticated_client
    customer = _customer(client)
    for bad in ("", "   ", "x" * 121):
        assert client.post(_url(customer), json={"name": bad}).status_code == 422, bad
    assert client.post(_url(customer), json={}).status_code == 422
    row = client.post(_url(customer), json={"name": "x" * 120}).json()
    assert len(row["name"]) == 120
    assert client.patch(f"{_url(customer)}/{row['id']}", json={"name": "   "}).status_code == 422
    assert client.post(_url(customer), json={"name": "Ok", "role": "y" * 121}).status_code == 422
    assert client.post(_url(customer), json={"name": "Ok", "color": "red"}).status_code == 422


def test_a_client_holds_at_most_fifty_professionals(authenticated_client: TestClient):
    client = authenticated_client
    customer = _customer(client)
    with TestingSession() as db:
        owner = db.get(Client, uuid.UUID(customer["id"]))
        db.add_all([Professional(agency_id=owner.agency_id, client_id=owner.id, name=f"P{i}") for i in range(50)])
        db.commit()
    refused = client.post(_url(customer), json={"name": "One too many"})
    assert refused.status_code == 409 and "50" in refused.json()["detail"]
    assert len(client.get(_url(customer)).json()) == 50
    # The limit is per client.
    other = _customer(client, "Other Co")
    assert client.post(_url(other), json={"name": "Fine"}).status_code == 201
    # Deleting one makes room again.
    first = client.get(_url(customer)).json()[0]
    assert client.delete(f"{_url(customer)}/{first['id']}").status_code == 204
    assert client.post(_url(customer), json={"name": "Now it fits"}).status_code == 201


def test_professionals_of_another_client_or_agency_are_out_of_reach(authenticated_client: TestClient):
    client = authenticated_client
    mine = _customer(client, "Mine Co")
    theirs = _customer(client, "Theirs Co")
    row = client.post(_url(theirs), json={"name": "Theirs"}).json()

    # Another client of the same agency: the id is not found under this client.
    assert client.patch(f"{_url(mine)}/{row['id']}", json={"name": "Hijacked"}).status_code == 404
    assert client.delete(f"{_url(mine)}/{row['id']}").status_code == 404
    assert client.get(_url(mine)).json() == []
    assert client.get(_url(theirs)).json()[0]["name"] == "Theirs"

    # Another agency: the client itself is not visible.
    login_legacy_owner(client)
    assert client.get(_url(theirs)).status_code == 404
    assert client.post(_url(theirs), json={"name": "Intruder"}).status_code == 404
    assert client.patch(f"{_url(theirs)}/{row['id']}", json={"name": "Hijacked"}).status_code == 404
    assert client.delete(f"{_url(theirs)}/{row['id']}").status_code == 404
    with TestingSession() as db:
        assert db.get(Professional, uuid.UUID(row["id"])).name == "Theirs"


def test_the_agency_needs_a_session(authenticated_client: TestClient):
    customer = _customer(authenticated_client)
    bare = TestClient(authenticated_client.app)
    assert bare.get(_url(customer)).status_code == 401
    assert bare.post(_url(customer), json={"name": "Nope"}).status_code == 401


def test_deleting_a_client_removes_its_professionals(authenticated_client: TestClient):
    client = authenticated_client
    customer = _customer(client)
    row = client.post(_url(customer), json={"name": "Gone soon"}).json()
    assert client.delete(f"/api/clients/{customer['id']}").status_code == 204
    with TestingSession() as db:
        assert db.get(Professional, uuid.UUID(row["id"])) is None


def test_an_api_token_reads_and_writes_only_with_the_matching_scope(authenticated_client: TestClient):
    client = authenticated_client
    customer = _customer(client)

    def token(scopes: list[str]) -> dict:
        integration = client.post("/api/integrations", json={"name": ",".join(scopes), "scopes": scopes}).json()
        issued = client.post(f"/api/integrations/{integration['id']}/tokens", json={"expires_in_days": 30}).json()
        return {"Authorization": f"Bearer {issued['token']}"}

    bare = TestClient(client.app)
    reader = token(["professionals.read"])
    writer = token(["professionals.manage"])
    assert bare.get(_url(customer), headers=reader).status_code == 200
    assert bare.post(_url(customer), headers=reader, json={"name": "Nope"}).status_code == 403
    assert bare.get(_url(customer), headers=writer).status_code == 403
    created = bare.post(_url(customer), headers=writer, json={"name": "Via token"})
    assert created.status_code == 201, created.text
    row_url = f"{_url(customer)}/{created.json()['id']}"
    assert bare.patch(row_url, headers=reader, json={"name": "Nope"}).status_code == 403
    assert bare.patch(row_url, headers=writer, json={"name": "Renamed"}).status_code == 200
    assert bare.delete(row_url, headers=reader).status_code == 403
    assert bare.delete(row_url, headers=writer).status_code == 204


# --- portal ---------------------------------------------------------------------------


def test_a_portal_admin_manages_the_professionals(authenticated_client: TestClient):
    client = authenticated_client
    customer, url = _portal(client)
    assert client.get(url).json() == []
    created = client.post(url, json={"name": "Dr. Ruiz", "role": "Dentist", "slot_minutes": 20, "weekly_hours": WEEK})
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["client_id"] == customer["id"] and row["slot_minutes"] == 20 and list(row["weekly_hours"]) == list(DAYS)

    changed = client.patch(f"{url}/{row['id']}", json={"name": "Dra. Ruiz", "is_active": False})
    assert changed.status_code == 200 and changed.json()["name"] == "Dra. Ruiz" and changed.json()["is_active"] is False
    assert client.get(url).json()[0]["name"] == "Dra. Ruiz"
    # The agency sees the same row.
    assert client.get(_url(customer)).json()[0]["id"] == row["id"]

    assert client.patch(f"{url}/{row['id']}", json={"weekly_hours": {"mon": [["10:00", "09:00"]]}}).status_code == 422
    assert client.delete(f"{url}/{row['id']}").status_code == 204
    assert client.get(url).json() == []
    assert client.delete(f"{url}/{row['id']}").status_code == 404


def test_a_portal_agent_can_read_but_not_write(authenticated_client: TestClient):
    client = authenticated_client
    customer, url = _portal(client)
    row = client.post(url, json={"name": "Dr. Ruiz"}).json()
    agent = _agent_session(client, customer)

    listed = agent.get(url)
    assert listed.status_code == 200 and [item["id"] for item in listed.json()] == [row["id"]]
    assert agent.post(url, json={"name": "Nope"}).json() == FORBIDDEN
    assert agent.patch(f"{url}/{row['id']}", json={"name": "Nope"}).json() == FORBIDDEN
    assert agent.delete(f"{url}/{row['id']}").json() == FORBIDDEN
    assert client.get(url).json()[0]["name"] == "Dr. Ruiz"


def test_the_portal_function_is_off_by_default_and_closes_every_route(authenticated_client: TestClient):
    client = authenticated_client
    customer, url = _portal(client, on=False)
    assert customer["portal_features"]["professionals"] is False
    row = client.post(_url(customer), json={"name": "Seeded by the agency"}).json()  # the agency is never gated
    calls = [
        client.get(url),
        client.post(url, json={"name": "Nope"}),
        client.patch(f"{url}/{row['id']}", json={"name": "Nope"}),
        client.delete(f"{url}/{row['id']}"),
    ]
    for response in calls:
        assert response.status_code == 403 and response.json() == NO_FEATURE
    assert client.get(f"/api/portal/{customer['portal_slug']}/me").json()["features"].count("professionals") == 0

    assert client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_features": {"professionals": True}}).status_code == 200
    assert client.get(url).status_code == 200
    assert "professionals" in client.get(f"/api/portal/{customer['portal_slug']}/me").json()["features"]
    # Switching it off again closes the door but keeps the rows.
    client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_features": {"professionals": False}})
    assert client.get(url).json() == NO_FEATURE
    assert client.get(_url(customer)).json()[0]["id"] == row["id"]


def test_the_session_lists_the_new_permissions_for_admins_only(authenticated_client: TestClient):
    client = authenticated_client
    customer, _ = _portal(client)
    slug = customer["portal_slug"]
    me = client.get(f"/api/portal/{slug}/me").json()
    assert {"professionals.manage", "client.manage"} <= set(me["permissions"])
    agent = _agent_session(client, customer)
    assert not {"professionals.manage", "client.manage"} & set(agent.get(f"/api/portal/{slug}/me").json()["permissions"])
    # The mobile session carries the same keys as plain strings.
    mobile = client.post("/api/mobile/sign-in", json={"email": f"ana@{slug}.com", "password": "secure-portal"})
    assert mobile.status_code == 200, mobile.text
    assert "professionals" in mobile.json()["features"] and "professionals.manage" in mobile.json()["permissions"]


def test_a_portal_never_reaches_another_clients_professionals(authenticated_client: TestClient):
    client = authenticated_client
    other = _customer(client, "Other Co")
    foreign = client.post(_url(other), json={"name": "Foreign"}).json()
    customer, url = _portal(client)
    assert client.get(url).json() == []
    assert client.patch(f"{url}/{foreign['id']}", json={"name": "Hijacked"}).status_code == 404
    assert client.delete(f"{url}/{foreign['id']}").status_code == 404
    assert client.patch(f"{url}/{ZERO}", json={"name": "Nobody"}).status_code == 404
    # The client id is never taken from the body, so a row cannot be planted elsewhere.
    planted = client.post(url, json={"name": "Mine", "client_id": other["id"], "agency_id": ZERO}).json()
    assert planted["client_id"] == customer["id"]
    assert [row["name"] for row in client.get(_url(other)).json()] == ["Foreign"]

    # A session of one portal is refused on another portal's URL.
    _, other_url = _portal(client, "Second Co")
    ana = TestClient(client.app)
    assert ana.post(f"/api/portal/{customer['portal_slug']}/login", json={"email": f"ana@{customer['portal_slug']}.com", "password": "secure-portal"}).status_code == 200
    assert ana.get(other_url).status_code == 401


def test_the_portal_needs_a_session(authenticated_client: TestClient):
    _, url = _portal(authenticated_client)
    bare = TestClient(authenticated_client.app)
    assert bare.get(url).status_code == 401
    assert bare.post(url, json={"name": "Nope"}).status_code == 401
