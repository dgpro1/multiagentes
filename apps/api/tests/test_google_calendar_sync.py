"""Unit tests for Google Calendar synchronization on appointment creation."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
import uuid
from zoneinfo import ZoneInfo
import pytest

from app.models import Agency, Agent, Appointment, CalendarMember, Client, Contact, Conversation, Professional, Service, now_utc
from app.services.calendar import sync_appointment_to_google
from conftest import TestingSession


@pytest.fixture
def db_session():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


def test_sync_appointment_to_google(db_session):
    async def run_test():
        agency = Agency(name="Agency Test", slug="agency-" + str(uuid.uuid4())[:8])
        db_session.add(agency)
        db_session.flush()

        client = Client(
            agency_id=agency.id,
            name="Dental Marbella",
            portal_slug="marbella-" + str(uuid.uuid4())[:8],
            timezone="America/Santiago",
        )
        db_session.add(client)
        db_session.flush()

        member = CalendarMember(
            agency_id=agency.id,
            client_id=client.id,
            name="Dr. Jorge",
            role="Ortodoncia",
            status="connected",
            google_email="info.danielgarciapro@gmail.com",
            calendar_id="primary",
            connect_expires_at=now_utc() + timedelta(days=7),
        )
        db_session.add(member)
        db_session.flush()

        contact = Contact(client_id=client.id, name="Daniel Garcia", phone="+56911223344")
        db_session.add(contact)
        db_session.flush()

        tz = ZoneInfo("America/Santiago")
        start = datetime(2026, 9, 28, 12, 0, 0, tzinfo=tz)
        end = datetime(2026, 9, 28, 12, 30, 0, tzinfo=tz)

        appt = Appointment(
            agency_id=agency.id,
            client_id=client.id,
            contact_id=contact.id,
            title="Evaluación Dental Inicial",
            start_time=start,
            end_time=end,
            duration_minutes=30,
            status="confirmed",
            notes="Agendado automáticamente",
        )
        db_session.add(appt)
        db_session.flush()

        with patch("app.services.google_calendar.configured", return_value=True), \
             patch("app.services.calendar.access_token", new_callable=AsyncMock, return_value="mock_token_123"), \
             patch("app.services.google_calendar.create_event", new_callable=AsyncMock) as mock_create_event:

            mock_create_event.return_value = {"id": "gcal_event_999", "status": "confirmed"}

            result = await sync_appointment_to_google(db_session, client, appt)

            assert result is not None
            assert result["id"] == "gcal_event_999"
            mock_create_event.assert_awaited_once()

            call_args = mock_create_event.call_args
            assert call_args[0][0] == "mock_token_123"
            assert call_args[0][1] == "primary"
            event_payload = call_args[0][2]
            assert "Daniel Garcia" in event_payload["summary"]
            assert "+56911223344" in event_payload["description"]
            assert event_payload["start"]["timeZone"] == "America/Santiago"
            assert event_payload["end"]["timeZone"] == "America/Santiago"

    asyncio.run(run_test())
