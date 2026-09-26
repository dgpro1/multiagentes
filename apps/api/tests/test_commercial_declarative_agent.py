"""Tests for the declarative commercial agent engine (Sub-Fase 5).

Verifies:
- Prompt-as-configuration declarative tool filtering and context hydration.
- Anti-regression pipeline stage movement.
- Real name requirement guardrail on appointment booking.
- Rescheduling appointments by list index.
- Escalate to human with reason only.
- Intentional silence safe delivery without social handover error.
- Context hydration with timezone, 14-day temporal table, and CLP currency formatting.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock
import uuid
from zoneinfo import ZoneInfo
import pytest

from app.models import Agency, Agent, Client, Contact, Conversation, Message, PipelineStage, Professional, Service, now_utc
from app.services.crm_prompt_hydrator import (
    build_agent_context, declared_tools, format_currency_clp, has_declarative_tools, hydrate_commercial_prompt
)
from app.services.tools.commercial_tools import CommercialEffects, build_commercial_tools, apply_commercial_effects
from app.services.appointments import create_appointment
from app.schemas_appointments import AppointmentCreate
from conftest import TestingSession


@pytest.fixture
def db_session():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


def make_client(db, name="Dental Marbella"):
    agency = Agency(name="Agency " + name, slug="agency-" + str(uuid.uuid4())[:8])
    db.add(agency)
    db.flush()
    client = Client(
        agency_id=agency.id,
        name=name,
        portal_slug="client-" + str(uuid.uuid4())[:8],
        timezone="America/Santiago",
        address="Dinamarca 621, Temuco"
    )
    db.add(client)
    db.flush()
    agent = Agent(
        client_id=client.id,
        agency_id=agency.id,
        name="Anita",
        instructions="",
        model="gpt-4.1-mini",
        provider="openrouter"
    )
    db.add(agent)
    db.flush()
    return agency, client, agent


ANITA_PROMPT_SNIPPET = """
[FECHA Y HORA ACTUAL DEL NEGOCIO]
[CATÁLOGO OFICIAL DE SERVICIOS Y TARIFAS]
[FICHA COMERCIAL DEL PROSPECTO / CLIENTE]
[CITAS ACTIVAS PROGRAMADAS PARA ESTE CLIENTE]
[NOTAS E INTERVENCIONES PREVIAS DEL EQUIPO HUMANO]
[UBICACIÓN Y DATOS DEL NEGOCIO]

Instrucciones:
1. Usa [Herramienta: check_calendar_availability] para buscar cupos.
2. Usa [Herramienta: update_contact_info] con el nombre del paciente.
3. Usa [Herramienta: add_lead_tag] con "Ortodoncia".
4. Mueve con [Herramienta: move_lead_stage] a [Etapa: Descubrimiento].
5. Reserva con [Herramienta: book_calendar_appointment].
6. Reprograma con [Herramienta: reschedule_appointment].
7. Anota con [Herramienta: add_internal_note].
8. Escala con [Herramienta: escalate_to_human].
9. Si no debes responder, llama a [Herramienta: stay_silent] o responde [SILENCIO].
"""


def test_declared_tools_and_blocks_parsing():
    assert has_declarative_tools(ANITA_PROMPT_SNIPPET) is True
    tools = declared_tools(ANITA_PROMPT_SNIPPET)
    expected = [
        "check_calendar_availability",
        "update_contact_info",
        "add_lead_tag",
        "move_lead_stage",
        "book_calendar_appointment",
        "reschedule_appointment",
        "add_internal_note",
        "escalate_to_human",
        "stay_silent",
    ]
    assert tools == expected


def test_currency_formatting():
    assert format_currency_clp(0) == "$0 (Sin costo)"
    assert format_currency_clp(99990) == "$99.990"
    assert format_currency_clp(199990) == "$199.990"
    assert format_currency_clp(35000) == "$35.000"


def test_build_agent_context_declarative_filtering(db_session):
    agency, client, agent = make_client(db_session, "Dental Marbella")
    agent.instructions = ANITA_PROMPT_SNIPPET
    db_session.flush()

    contact = Contact(client_id=client.id, name="Camila Soto", phone="+56912345678")
    db_session.add(contact)
    db_session.flush()

    convo = Conversation(
        agency_id=agency.id,
        client_id=client.id,
        agent_id=agent.id,
        contact_id=contact.id,
        channel="whatsapp",
        mode="ai",
        status="open"
    )
    db_session.add(convo)
    db_session.flush()

    ctx_res = build_agent_context(db_session, agent, convo, agent.instructions, "whatsapp", contact=contact)
    assert ctx_res.is_commercial is True
    assert ctx_res.effects is not None
    # Check that all 9 tools were built
    spec_names = [s.name for s in ctx_res.extra_specs]
    assert "check_calendar_availability" in spec_names
    assert "book_calendar_appointment" in spec_names
    assert "move_lead_stage" in spec_names
    assert "add_lead_tag" in spec_names
    assert "cancel_appointment" not in spec_names  # Not cited in snippet, so not built!

    # Check temporal block in hydrated text
    assert "[FECHA Y HORA ACTUAL DEL NEGOCIO]" in ctx_res.system_content
    assert "America/Santiago" in ctx_res.system_content
    assert "Referencia de calendario oficial (próximos 14 días):" in ctx_res.system_content


def test_anti_regression_pipeline_stage(db_session):
    agency, client, agent = make_client(db_session, "Pipeline Client")

    stage_desc = PipelineStage(client_id=client.id, name="Descubrimiento", position=0, color="#3b82f6")
    stage_cita = PipelineStage(client_id=client.id, name="Cita Agendada", position=1, color="#22c55e")
    db_session.add_all([stage_desc, stage_cita])
    db_session.flush()

    contact = Contact(client_id=client.id, name="Pedro Morales")
    db_session.add(contact)
    db_session.flush()

    convo = Conversation(
        agency_id=agency.id,
        client_id=client.id,
        agent_id=agent.id,
        contact_id=contact.id,
        channel="whatsapp",
        pipeline_stage_id=stage_cita.id
    )
    db_session.add(convo)
    db_session.flush()

    effects = CommercialEffects()
    tools = build_commercial_tools(db_session, client, convo, agent, contact, ["move_lead_stage"], effects)
    move_spec = next(s for s in tools if s.name == "move_lead_stage")

    # Attempt to downgrade to "Descubrimiento"
    res, is_err = move_spec.handler({"stage_name": "Descubrimiento"})
    assert is_err is False
    assert "mantenida en 'Cita Agendada'" in res
    assert len(effects.pipeline_stage) == 0  # Not modified!


def test_booking_guardrail_requires_real_name(db_session):
    agency, client, agent = make_client(db_session, "Booking Client")

    # Contact with only unverified WhatsApp profile name
    contact = Contact(client_id=client.id, name=None, whatsapp_contact_name="Cami ✨")
    db_session.add(contact)
    db_session.flush()

    convo = Conversation(agency_id=agency.id, client_id=client.id, agent_id=agent.id, contact_id=contact.id, channel="whatsapp")
    db_session.add(convo)
    db_session.flush()

    effects = CommercialEffects()
    tools = build_commercial_tools(db_session, client, convo, agent, contact, ["book_calendar_appointment", "update_contact_info"], effects)
    book_spec = next(s for s in tools if s.name == "book_calendar_appointment")
    update_spec = next(s for s in tools if s.name == "update_contact_info")

    # Booking without real name should fail with instruction to ask for name
    res, is_err = book_spec.handler({"start_time": "2026-09-28T16:00:00-03:00", "title": "Evaluación Dental"})
    assert is_err is True
    assert "nombre real" in res.lower()

    # Update real name
    res_up, is_err_up = update_spec.handler({"name": "Camila Soto"})
    assert is_err_up is False
    assert contact.name == "Camila Soto"

    # Now booking should proceed
    res_book, is_err_book = book_spec.handler({"start_time": "2026-09-28T16:00:00-03:00", "title": "Evaluación Dental"})
    assert is_err_book is False
    assert "agendada exitosamente" in res_book.lower()


def test_reschedule_appointment_by_index(db_session):
    agency, client, agent = make_client(db_session, "Reschedule Client")

    contact = Contact(client_id=client.id, name="Juan Pérez")
    db_session.add(contact)
    db_session.flush()

    convo = Conversation(agency_id=agency.id, client_id=client.id, agent_id=agent.id, contact_id=contact.id, channel="whatsapp")
    db_session.add(convo)
    db_session.flush()

    # Create active future appointment
    tz = ZoneInfo("America/Santiago")
    future_time = datetime.now(tz) + timedelta(days=2)
    apt = create_appointment(
        db_session, client,
        AppointmentCreate(contact_id=contact.id, start_time=future_time, title="Control Ortodoncia", duration_minutes=30)
    )

    effects = CommercialEffects()
    tools = build_commercial_tools(db_session, client, convo, agent, contact, ["reschedule_appointment"], effects)
    resched_spec = next(s for s in tools if s.name == "reschedule_appointment")

    new_time = datetime.now(tz) + timedelta(days=3)
    new_time_iso = new_time.isoformat()
    res, is_err = resched_spec.handler({"appointment_number": 1, "new_start_time": new_time_iso})
    assert is_err is False
    assert "reprogramada exitosamente" in res.lower()


def test_escalate_to_human_with_reason_only(db_session):
    agency, client, agent = make_client(db_session, "Escalate Client")

    contact = Contact(client_id=client.id, name="Matías Silva")
    db_session.add(contact)
    db_session.flush()

    convo = Conversation(agency_id=agency.id, client_id=client.id, agent_id=agent.id, contact_id=contact.id, channel="whatsapp")
    db_session.add(convo)
    db_session.flush()

    effects = CommercialEffects()
    tools = build_commercial_tools(db_session, client, convo, agent, contact, ["escalate_to_human"], effects)
    esc_spec = next(s for s in tools if s.name == "escalate_to_human")

    res, is_err = esc_spec.handler({"reason": "Urgencia dental por dolor agudo"})
    assert is_err is False
    assert len(effects.escalation) == 1
    assert effects.escalation[0].reason == "Urgencia dental por dolor agudo"


def test_stay_silent_effect(db_session):
    agency, client, agent = make_client(db_session, "Silent Client")
    contact = Contact(client_id=client.id, name="Test")
    convo = Conversation(agency_id=agency.id, client_id=client.id, agent_id=agent.id, channel="whatsapp")
    effects = CommercialEffects()
    tools = build_commercial_tools(db_session, client, convo, agent, contact, ["stay_silent"], effects)
    silent_spec = next(s for s in tools if s.name == "stay_silent")

    res, is_err = silent_spec.handler({})
    assert is_err is False
    assert effects.is_silent is True
