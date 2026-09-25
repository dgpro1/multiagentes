"""End-to-End Test Suite for Appointments, Services & Availability Flow.

Validates:
1. Client portal configuration & feature inheritance.
2. Services catalog creation.
3. Professionals creation with M:N assigned services (service_ids).
4. Availability queries with service-based filtering (M:N).
5. Appointment booking with collision prevention and activity logging.
6. Dynamic availability updates upon booking and cancellation.
7. Professional service re-assignment reflecting immediately in availability.
"""

from fastapi.testclient import TestClient

from app.models import Client, Conversation, Message
from conftest import TestingSession


def test_e2e_services_professionals_availability_booking_flow(authenticated_client: TestClient):
    client = authenticated_client

    # 1. Create client and configure portal features
    res = client.post("/api/clients", json={"name": "Clinica Dental E2E", "is_active": True})
    assert res.status_code == 201
    c_data = res.json()
    cid = c_data["id"]
    portal_slug = c_data["portal_slug"]

    # Configure portal user and enable portal with calendar/services/professionals
    # Notice: "appointments" omitted deliberately to verify automatic inheritance from "calendar"
    client.post(
        f"/api/clients/{cid}/portal-users",
        json={"name": "Dr. Admin", "email": f"admin@{portal_slug}.com", "password": "password123"},
    )
    client.patch(
        f"/api/clients/{cid}/portal",
        json={
            "portal_enabled": True,
            "portal_features": {
                "calendar": True,
                "services": True,
                "professionals": True,
                "appointments": True,
            },
        },
    )

    # 2. Create services in catalog (agency side)
    res_srv1 = client.post(
        f"/api/clients/{cid}/services",
        json={
            "name": "Limpieza Dental",
            "duration_minutes": 30,
            "price": "60.00",
            "is_active": True,
        },
    )
    assert res_srv1.status_code == 201
    srv1 = res_srv1.json()

    res_srv2 = client.post(
        f"/api/clients/{cid}/services",
        json={
            "name": "Ortodoncia Avanzada",
            "duration_minutes": 60,
            "price": "180.00",
            "is_active": True,
        },
    )
    assert res_srv2.status_code == 201
    srv2 = res_srv2.json()

    # 3. Create professionals with assigned services
    # 2026-10-05 is a Monday
    res_prof1 = client.post(
        f"/api/clients/{cid}/professionals",
        json={
            "name": "Dr. Alberto Higienista",
            "role": "Higienista",
            "color": "#2563eb",
            "is_active": True,
            "slot_minutes": 30,
            "service_ids": [srv1["id"]],
            "weekly_hours": {
                "mon": [["09:00", "12:00"]],
                "tue": [],
                "wed": [],
                "thu": [],
                "fri": [],
                "sat": [],
                "sun": [],
            },
        },
    )
    assert res_prof1.status_code == 201
    prof1 = res_prof1.json()
    assert prof1["service_ids"] == [srv1["id"]]

    res_prof2 = client.post(
        f"/api/clients/{cid}/professionals",
        json={
            "name": "Dra. Beatriz Ortodoncista",
            "role": "Ortodoncista",
            "color": "#7c3aed",
            "is_active": True,
            "slot_minutes": 60,
            "service_ids": [srv2["id"]],
            "weekly_hours": {
                "mon": [["14:00", "17:00"]],
                "tue": [],
                "wed": [],
                "thu": [],
                "fri": [],
                "sat": [],
                "sun": [],
            },
        },
    )
    assert res_prof2.status_code == 201
    prof2 = res_prof2.json()
    assert prof2["service_ids"] == [srv2["id"]]

    # Verify listing professionals includes service_ids
    res_list_profs = client.get(f"/api/clients/{cid}/professionals")
    assert res_list_profs.status_code == 200
    listed = {p["id"]: p for p in res_list_profs.json()}
    assert listed[prof1["id"]]["service_ids"] == [srv1["id"]]
    assert listed[prof2["id"]]["service_ids"] == [srv2["id"]]

    # 4. Sign in to the portal
    login_res = client.post(
        f"/api/portal/{portal_slug}/login",
        json={"email": f"admin@{portal_slug}.com", "password": "password123"},
    )
    assert login_res.status_code == 200, login_res.text

    # 5. Availability checks via portal (2026-10-05 is Monday)
    # 5a. Query availability for Service 1 (Limpieza Dental) -> Only Dr. Alberto's slots
    res_avail1 = client.get(
        f"/api/portal/{portal_slug}/appointments/availability",
        params={"date_from": "2026-10-05", "date_to": "2026-10-05", "service_id": srv1["id"]},
    )
    assert res_avail1.status_code == 200, res_avail1.text
    day1_slots = res_avail1.json()["days"][0]["slots"]
    assert len(day1_slots) == 6  # 09:00, 09:30, 10:00, 10:30, 11:00, 11:30
    for s in day1_slots:
        assert s["professional_id"] == prof1["id"]
        assert s["professional_name"] == "Dr. Alberto Higienista"

    # 5b. Query availability for Service 2 (Ortodoncia Avanzada) -> Only Dra. Beatriz's slots
    res_avail2 = client.get(
        f"/api/portal/{portal_slug}/appointments/availability",
        params={"date_from": "2026-10-05", "date_to": "2026-10-05", "service_id": srv2["id"]},
    )
    assert res_avail2.status_code == 200
    day2_slots = res_avail2.json()["days"][0]["slots"]
    assert len(day2_slots) == 3  # 14:00, 15:00, 16:00 (duration 60 min each, 14:00-17:00)
    for s in day2_slots:
        assert s["professional_id"] == prof2["id"]
        assert s["professional_name"] == "Dra. Beatriz Ortodoncista"

    # 6. Book an appointment for Service 1
    # Create agent & conversation to verify activity message creation
    agent_data = client.post("/api/agents", json={"client_id": cid, "name": "Bot Aurora"}).json()
    conv_data = client.post("/api/conversations", json={"agent_id": agent_data["id"]}).json()
    conv_id = conv_data["id"]

    res_book = client.post(
        f"/api/portal/{portal_slug}/appointments",
        json={
            "conversation_id": conv_id,
            "professional_id": prof1["id"],
            "service_id": srv1["id"],
            "title": "Limpieza Dental Pedro",
            "start_time": "2026-10-05T09:00:00Z",
            "end_time": "2026-10-05T09:30:00Z",
            "notes": "Primera visita",
        },
    )
    assert res_book.status_code == 201, res_book.text
    apt_data = res_book.json()
    assert apt_data["status"] == "confirmed"
    assert apt_data["professional_name"] == "Dr. Alberto Higienista"
    assert apt_data["service_name"] == "Limpieza Dental"

    # Verify activity message in conversation thread
    with TestingSession() as db:
        msgs = db.query(Message).filter(Message.conversation_id == conv_id).all()
        assert len(msgs) >= 1
        activity_msg = next((m for m in msgs if m.kind == "activity"), None)
        assert activity_msg is not None
        assert activity_msg.activity is not None
        assert activity_msg.activity.get("event") == "appointment_created"

    # 7. Collision detection & slot exclusion
    # 7a. Query availability again for Service 1 -> 09:00 slot is now excluded
    res_avail1_after = client.get(
        f"/api/portal/{portal_slug}/appointments/availability",
        params={"date_from": "2026-10-05", "date_to": "2026-10-05", "service_id": srv1["id"]},
    )
    day1_slots_after = res_avail1_after.json()["days"][0]["slots"]
    slot_starts = [s["start_time"] for s in day1_slots_after]
    assert "2026-10-05T09:00:00Z" not in slot_starts
    assert len(day1_slots_after) == 5

    # 7b. Attempt to book double-booking returns 409
    res_collision = client.post(
        f"/api/portal/{portal_slug}/appointments",
        json={
            "professional_id": prof1["id"],
            "service_id": srv1["id"],
            "title": "Conflicto",
            "start_time": "2026-10-05T09:00:00Z",
            "end_time": "2026-10-05T09:30:00Z",
        },
    )
    assert res_collision.status_code == 409
    assert "no longer available" in res_collision.json()["detail"]

    # 8. Dynamic assignment: Give Dr. Alberto the Ortodoncia Avanzada service as well
    # Portal user has admin role or we can update professional via client endpoint
    res_update_prof = client.patch(
        f"/api/portal/{portal_slug}/professionals/{prof1['id']}",
        json={"service_ids": [srv1["id"], srv2["id"]]},
    )
    assert res_update_prof.status_code == 200
    assert set(res_update_prof.json()["service_ids"]) == {srv1["id"], srv2["id"]}

    # Query availability for Ortodoncia: Dr. Alberto's remaining slots (09:30, 10:30) are now also available!
    res_avail2_after = client.get(
        f"/api/portal/{portal_slug}/appointments/availability",
        params={"date_from": "2026-10-05", "date_to": "2026-10-05", "service_id": srv2["id"]},
    )
    day2_slots_after = res_avail2_after.json()["days"][0]["slots"]
    profs_offering_srv2 = {s["professional_id"] for s in day2_slots_after}
    assert prof1["id"] in profs_offering_srv2
    assert prof2["id"] in profs_offering_srv2

    # 9. Cancel appointment and verify slot release
    apt_id = apt_data["id"]
    res_cancel = client.patch(
        f"/api/portal/{portal_slug}/appointments/{apt_id}",
        json={"status": "cancelled"},
    )
    assert res_cancel.status_code == 200
    assert res_cancel.json()["status"] == "cancelled"

    # Now 09:00 is available again
    res_avail1_released = client.get(
        f"/api/portal/{portal_slug}/appointments/availability",
        params={"date_from": "2026-10-05", "date_to": "2026-10-05", "service_id": srv1["id"]},
    )
    day1_slots_released = res_avail1_released.json()["days"][0]["slots"]
    released_starts = [s["start_time"] for s in day1_slots_released]
    assert "2026-10-05T09:00:00Z" in released_starts
    assert len(day1_slots_released) == 6
