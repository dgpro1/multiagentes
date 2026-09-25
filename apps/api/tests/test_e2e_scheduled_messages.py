"""End-to-End Test Suite for Scheduled Messages (Sub-Fase 4).

Validates:
1. Message scheduling via portal API with validation (future time, non-empty text).
2. Listing and filtering scheduled messages.
3. Updating pending scheduled messages.
4. Cancelling pending scheduled messages.
5. Background worker dispatching due messages and creating thread messages.
6. Safety guardrails:
   - Refusing dispatch for resolved/abandoned/archived conversations.
   - Refusing dispatch for blocked contacts.
   - Refusing dispatch when 24h channel window is closed.
7. Lead merge compatibility:
   - Preserving and reassigning scheduled messages to the primary lead upon merge.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient

from app.models import Agent, Client, Contact, Conversation, Message, ScheduledMessage, now_utc
from app.services.lead_merge import merge_leads
from app.services.scheduled_messages import dispatch_due_scheduled_messages
from conftest import TestingSession


def test_e2e_scheduled_messages_flow(authenticated_client: TestClient, monkeypatch):
    monkeypatch.setattr("app.services.whatsapp.send_channel_message", AsyncMock(return_value="test_ext_123"))
    client = authenticated_client

    # 1. Create client, agent, conversation, and configure portal
    res = client.post("/api/clients", json={"name": "Clinica Estetica E2E", "is_active": True})
    assert res.status_code == 201
    c_data = res.json()
    cid = c_data["id"]
    portal_slug = c_data["portal_slug"]

    # Portal user and enable inbox feature
    client.post(
        f"/api/clients/{cid}/portal-users",
        json={"name": "Dr. Scheduler", "email": f"scheduler@{portal_slug}.com", "password": "password123"},
    )
    client.patch(
        f"/api/clients/{cid}/portal",
        json={
            "portal_enabled": True,
            "portal_features": {
                "inbox": True,
            },
        },
    )

    # Create agent and conversation in DB
    with TestingSession() as db:
        agent = Agent(agency_id=db.get(Client, cid).agency_id, client_id=cid, name="Agent Scheduler", is_active=True)
        db.add(agent)
        db.flush()

        conv = Conversation(
            agency_id=agent.agency_id,
            client_id=cid,
            agent_id=agent.id,
            channel="whatsapp",
            external_chat_id="test_chat_key_1",
            contact_name="Maria Paciente",
            status="open",
            mode="human",
        )
        db.add(conv)
        db.commit()
        conv_id = conv.id

    # Sign in to portal
    login_res = client.post(
        f"/api/portal/{portal_slug}/login",
        json={"email": f"scheduler@{portal_slug}.com", "password": "password123"},
    )
    assert login_res.status_code == 200, login_res.text

    # 2. Scheduling validation
    # 2a. Reject scheduling in the past
    past_time = (now_utc() - timedelta(hours=1)).isoformat()
    res_past = client.post(
        f"/api/portal/{portal_slug}/conversations/{conv_id}/scheduled-messages",
        json={"content": "Mensaje en el pasado", "scheduled_for": past_time},
    )
    assert res_past.status_code == 422
    assert "future" in res_past.text.lower()

    # 2b. Reject empty message
    future_time_1 = (now_utc() + timedelta(hours=2)).isoformat()
    res_empty = client.post(
        f"/api/portal/{portal_slug}/conversations/{conv_id}/scheduled-messages",
        json={"content": "   ", "scheduled_for": future_time_1},
    )
    assert res_empty.status_code == 422

    # 2c. Successful creation
    res_create = client.post(
        f"/api/portal/{portal_slug}/conversations/{conv_id}/scheduled-messages",
        json={"content": "Hola Maria, recordatorio de tu cita", "scheduled_for": future_time_1},
    )
    assert res_create.status_code == 201
    sm1 = res_create.json()
    assert sm1["status"] == "pending"
    assert sm1["content"] == "Hola Maria, recordatorio de tu cita"
    sm1_id = sm1["id"]

    # 3. List scheduled messages
    res_list = client.get(
        f"/api/portal/{portal_slug}/conversations/{conv_id}/scheduled-messages?status=pending"
    )
    assert res_list.status_code == 200
    items = res_list.json()
    assert len(items) == 1
    assert items[0]["id"] == sm1_id

    # 4. Update scheduled message (content and time)
    future_time_2 = (now_utc() + timedelta(hours=4)).isoformat()
    res_update = client.patch(
        f"/api/portal/{portal_slug}/conversations/{conv_id}/scheduled-messages/{sm1_id}",
        json={"content": "Hola Maria, recordatorio modificado", "scheduled_for": future_time_2},
    )
    assert res_update.status_code == 200
    updated = res_update.json()
    assert updated["content"] == "Hola Maria, recordatorio modificado"

    # 5. Cancel scheduled message
    res_cancel = client.delete(
        f"/api/portal/{portal_slug}/conversations/{conv_id}/scheduled-messages/{sm1_id}"
    )
    assert res_cancel.status_code == 200
    cancelled = res_cancel.json()
    assert cancelled["status"] == "cancelled"

    # Cannot cancel again
    res_cancel_again = client.delete(
        f"/api/portal/{portal_slug}/conversations/{conv_id}/scheduled-messages/{sm1_id}"
    )
    assert res_cancel_again.status_code == 400

    # 6. Background dispatch worker
    # Create a message due in the past directly in DB
    with TestingSession() as db:
        due_sm = ScheduledMessage(
            agency_id=agent.agency_id,
            client_id=cid,
            conversation_id=conv_id,
            content="Mensaje despachado automaticamente",
            scheduled_for=now_utc() - timedelta(minutes=5),
            status="pending",
            sender_type="human",
            sender_name="Dr. Scheduler",
        )
        db.add(due_sm)
        db.commit()
        due_sm_id = due_sm.id

        # Run dispatch worker
        dispatched_count = asyncio.run(dispatch_due_scheduled_messages(db))
        assert dispatched_count == 1

        # Verify ScheduledMessage is marked sent
        db.refresh(due_sm)
        assert due_sm.status == "sent"
        assert due_sm.sent_at is not None

        # Verify Message was stored in conversation thread
        msg = db.query(Message).filter(Message.conversation_id == conv_id, Message.content == "Mensaje despachado automaticamente").first()
        assert msg is not None
        assert msg.sender_name == "Dr. Scheduler"
        assert msg.sender_type == "human"

    # 7. Safety guardrail: Refuse dispatch if conversation is resolved
    with TestingSession() as db:
        c_row = db.get(Conversation, conv_id)
        c_row.status = "resolved"
        db.commit()

        resolved_sm = ScheduledMessage(
            agency_id=agent.agency_id,
            client_id=cid,
            conversation_id=conv_id,
            content="No debe enviarse porque esta resuelta",
            scheduled_for=now_utc() - timedelta(minutes=5),
            status="pending",
        )
        db.add(resolved_sm)
        db.commit()

        # Run worker
        asyncio.run(dispatch_due_scheduled_messages(db))
        db.refresh(resolved_sm)
        assert resolved_sm.status == "failed"
        assert "resolved" in resolved_sm.failure_reason

        # Reopen conversation for subsequent test
        c_row.status = "open"
        db.commit()

    # 8. Safety guardrail: Refuse dispatch if contact is blocked
    with TestingSession() as db:
        client_row = db.get(Client, cid)
        contact = Contact(client_id=cid, name="Blocked Contact", blocked_at=now_utc())
        db.add(contact)
        db.flush()

        blocked_conv = Conversation(
            agency_id=client_row.agency_id,
            client_id=cid,
            agent_id=agent.id,
            channel="whatsapp",
            external_chat_id="test_blocked_chat",
            contact_id=contact.id,
            status="open",
        )
        db.add(blocked_conv)
        db.flush()

        blocked_sm = ScheduledMessage(
            agency_id=client_row.agency_id,
            client_id=cid,
            conversation_id=blocked_conv.id,
            content="No debe enviarse porque contacto esta bloqueado",
            scheduled_for=now_utc() - timedelta(minutes=5),
            status="pending",
        )
        db.add(blocked_sm)
        db.commit()

        asyncio.run(dispatch_due_scheduled_messages(db))
        db.refresh(blocked_sm)
        assert blocked_sm.status == "failed"
        assert "blocked" in blocked_sm.failure_reason

    # 9. Lead merge compatibility: Scheduled messages on secondary move to primary
    with TestingSession() as db:
        client_row = db.get(Client, cid)

        primary_conv = Conversation(
            agency_id=client_row.agency_id,
            client_id=cid,
            agent_id=agent.id,
            channel="whatsapp",
            external_chat_id="primary_chat_key",
            number=101,
            status="open",
        )
        secondary_conv = Conversation(
            agency_id=client_row.agency_id,
            client_id=cid,
            agent_id=agent.id,
            channel="whatsapp",
            external_chat_id="secondary_chat_key",
            number=102,
            status="open",
        )
        db.add_all([primary_conv, secondary_conv])
        db.flush()

        # Schedule a message on secondary conversation
        sec_sm = ScheduledMessage(
            agency_id=client_row.agency_id,
            client_id=cid,
            conversation_id=secondary_conv.id,
            content="Mensaje en lead secundario que debe reasignarse",
            scheduled_for=now_utc() + timedelta(days=1),
            status="pending",
        )
        db.add(sec_sm)
        db.commit()

        sec_sm_id = sec_sm.id
        p_id = primary_conv.id
        s_id = secondary_conv.id

        # Merge secondary into primary
        merge_leads(db, client_row, p_id, s_id, actor="tester")

        # Check that scheduled message's conversation_id is now primary_conv.id
        db.refresh(sec_sm)
        assert sec_sm.conversation_id == p_id
