"""Appointments & Availability test suite.

Tests CRUD, availability calculation, collision/double-booking rejection,
feature gating in portal, and lead merge reassignment.
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.models import Appointment, Client, Contact, Conversation, Professional, Service
from conftest import TestingSession


def _create_client(client: TestClient, name: str = "Dental Clinic") -> dict:
    res = client.post("/api/clients", json={"name": name, "is_active": True})
    assert res.status_code == 201
    return res.json()


def _create_professional(client: TestClient, client_id: str, name: str = "Dr. Smith") -> dict:
    res = client.post(
        f"/api/clients/{client_id}/professionals",
        json={
            "name": name,
            "role": "Dentist",
            "is_active": True,
            "slot_minutes": 30,
            "weekly_hours": {
                "mon": [["09:00", "12:00"], ["14:00", "17:00"]],
                "tue": [["09:00", "12:00"]],
            },
        },
    )
    assert res.status_code == 201
    return res.json()


def _create_service(client: TestClient, client_id: str, name: str = "Cleaning") -> dict:
    res = client.post(
        f"/api/clients/{client_id}/services",
        json={
            "name": name,
            "duration_minutes": 30,
            "price": "50.00",
            "is_active": True,
        },
    )
    assert res.status_code == 201
    return res.json()


def test_appointment_crud_and_collision_prevention(authenticated_client: TestClient):
    client = authenticated_client
    c = _create_client(client)
    cid = c["id"]
    prof1 = _create_professional(client, cid, "Dr. Ana")
    prof2 = _create_professional(client, cid, "Dr. Carlos")
    srv = _create_service(client, cid, "Root Canal")

    # 1. List initially empty
    res = client.get(f"/api/clients/{cid}/appointments")
    assert res.status_code == 200
    assert res.json() == []

    # 2. Create appointment for Prof 1
    # 2026-10-05 is a Monday
    start_1 = "2026-10-05T09:00:00Z"
    end_1 = "2026-10-05T09:30:00Z"
    res = client.post(
        f"/api/clients/{cid}/appointments",
        json={
            "professional_id": prof1["id"],
            "service_id": srv["id"],
            "title": "Ana - Root Canal",
            "start_time": start_1,
            "duration_minutes": 30,
            "notes": "Patient first visit",
        },
    )
    assert res.status_code == 201, res.text
    apt1 = res.json()
    assert apt1["status"] == "confirmed"
    assert apt1["professional_name"] == "Dr. Ana"
    assert apt1["service_name"] == "Root Canal"

    # 3. Collision: Attempting overlapping booking for the same professional returns 409
    res = client.post(
        f"/api/clients/{cid}/appointments",
        json={
            "professional_id": prof1["id"],
            "title": "Double book test",
            "start_time": "2026-10-05T09:15:00Z",
            "duration_minutes": 30,
        },
    )
    assert res.status_code == 409
    assert "no longer available" in res.json()["detail"]

    # 4. Overlapping booking on DIFFERENT professional succeeds
    res = client.post(
        f"/api/clients/{cid}/appointments",
        json={
            "professional_id": prof2["id"],
            "title": "Carlos - Cleaning",
            "start_time": "2026-10-05T09:00:00Z",
            "duration_minutes": 30,
        },
    )
    assert res.status_code == 201
    apt2 = res.json()
    assert apt2["professional_name"] == "Dr. Carlos"

    # 5. Update appointment: reschedule
    new_start = "2026-10-05T10:00:00Z"
    res = client.patch(
        f"/api/clients/{cid}/appointments/{apt1['id']}",
        json={"start_time": new_start, "notes": "Rescheduled to 10:00"},
    )
    assert res.status_code == 200
    updated = res.json()
    assert updated["notes"] == "Rescheduled to 10:00"
    dt = datetime.fromisoformat(updated["start_time"]).astimezone(timezone.utc)
    assert dt.hour == 10 and dt.minute == 0

    # 6. Former slot (09:00 - 09:30) is now free for Prof 1
    res = client.post(
        f"/api/clients/{cid}/appointments",
        json={
            "professional_id": prof1["id"],
            "title": "New booking in freed slot",
            "start_time": "2026-10-05T09:00:00Z",
            "duration_minutes": 30,
        },
    )
    assert res.status_code == 201

    # 7. Cancel appointment
    res = client.patch(
        f"/api/clients/{cid}/appointments/{apt1['id']}",
        json={"status": "cancelled"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "cancelled"

    # 8. Delete appointment
    res = client.delete(f"/api/clients/{cid}/appointments/{apt2['id']}")
    assert res.status_code == 204


def test_availability_calculation(authenticated_client: TestClient):
    client = authenticated_client
    c = _create_client(client)
    cid = c["id"]
    prof = _create_professional(client, cid, "Dr. Schedule")
    srv = _create_service(client, cid, "Consultation")

    # 2026-10-05 is a Monday: weekly_hours has [["09:00", "12:00"], ["14:00", "17:00"]]
    res = client.get(
        f"/api/clients/{cid}/appointments/availability?date_from=2026-10-05&date_to=2026-10-05&professional_id={prof['id']}&service_id={srv['id']}"
    )
    assert res.status_code == 200, res.text
    days = res.json()["days"]
    assert len(days) == 1
    slots = days[0]["slots"]
    # 09:00, 09:30, 10:00, 10:30, 11:00, 11:30 (6 slots) +
    # 14:00, 14:30, 15:00, 15:30, 16:00, 16:30 (6 slots) = 12 slots total
    assert len(slots) == 12

    # Book 09:30 - 10:00
    client.post(
        f"/api/clients/{cid}/appointments",
        json={
            "professional_id": prof["id"],
            "title": "Booked consultation",
            "start_time": "2026-10-05T09:30:00Z",
            "duration_minutes": 30,
        },
    )

    # Re-check availability: 09:30 slot should now be gone, 11 slots remaining
    res = client.get(
        f"/api/clients/{cid}/appointments/availability?date_from=2026-10-05&date_to=2026-10-05&professional_id={prof['id']}&service_id={srv['id']}"
    )
    assert res.status_code == 200
    updated_slots = res.json()["days"][0]["slots"]
    assert len(updated_slots) == 11
    assert not any("09:30:00" in s["start_time"] for s in updated_slots)


def test_portal_appointments_feature_gate_and_flow(authenticated_client: TestClient):
    client = authenticated_client
    customer = _create_client(client, "Portal Clinic")
    cid = customer["id"]
    slug = customer["portal_slug"]

    # Create portal user & enable portal
    client.post(f"/api/clients/{cid}/portal-users", json={"name": "Operator Joe", "email": f"joe@{slug}.com", "password": "password123"})
    client.patch(f"/api/clients/{cid}/portal", json={"portal_enabled": True})

    # Sign into portal
    portal_session = client.post(f"/api/portal/{slug}/login", json={"email": f"joe@{slug}.com", "password": "password123"})
    assert portal_session.status_code == 200

    # 1. By default, appointments feature is off -> 403
    res = client.get(f"/api/portal/{slug}/appointments")
    assert res.status_code == 403
    assert res.json()["detail"] == "This feature is not enabled for this portal"

    # 2. Turn appointments feature on
    client.patch(f"/api/clients/{cid}/portal", json={"portal_features": {"appointments": True}})

    # 3. Now portal access works
    res = client.get(f"/api/portal/{slug}/appointments")
    assert res.status_code == 200
    assert res.json() == []

    # 4. Portal user creates an appointment
    res = client.post(
        f"/api/portal/{slug}/appointments",
        json={
            "title": "Portal Booking",
            "start_time": "2026-10-06T10:00:00Z",
            "duration_minutes": 45,
            "notes": "Created from client portal",
        },
    )
    assert res.status_code == 201
    apt = res.json()
    assert apt["title"] == "Portal Booking"

    # 5. Portal user updates appointment
    res = client.patch(
        f"/api/portal/{slug}/appointments/{apt['id']}",
        json={"notes": "Updated from portal"},
    )
    assert res.status_code == 200
    assert res.json()["notes"] == "Updated from portal"

    # 6. Portal user deletes appointment
    res = client.delete(f"/api/portal/{slug}/appointments/{apt['id']}")
    assert res.status_code == 204


def test_lead_merge_moves_appointments(authenticated_client: TestClient):
    client = authenticated_client
    c = _create_client(client, "Merge Clinic")
    cid = c["id"]

    # Create agent and two conversations
    agent = client.post("/api/agents", json={"client_id": cid, "name": "Agent 1"}).json()
    conv1 = client.post("/api/conversations", json={"agent_id": agent["id"]}).json()
    conv2 = client.post("/api/conversations", json={"agent_id": agent["id"]}).json()

    with TestingSession() as db:
        c1 = db.get(Conversation, uuid.UUID(conv1["id"]))
        c2 = db.get(Conversation, uuid.UUID(conv2["id"]))
        c1.channel = "widget"
        c2.channel = "widget"
        db.commit()

    # Create appointment on conv2
    apt = client.post(
        f"/api/clients/{cid}/appointments",
        json={
            "conversation_id": conv2["id"],
            "title": "Appointment on Lead 2",
            "start_time": "2026-10-07T11:00:00Z",
            "duration_minutes": 30,
        },
    ).json()

    # Merge conv2 into conv1
    merge_res = client.post(
        f"/api/clients/{cid}/leads/merge",
        json={"primary_conversation_id": conv1["id"], "secondary_conversation_id": conv2["id"]},
    )
    assert merge_res.status_code == 200, merge_res.text

    # Verify appointment is now linked to conv1
    with TestingSession() as db:
        row = db.get(Appointment, uuid.UUID(apt["id"]))
        assert str(row.conversation_id) == conv1["id"]
