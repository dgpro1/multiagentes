"""Declarative commercial tools for autonomous CRM agents.

Builds tool specifications for the 9 commercial tools referenced in prompts:
- check_calendar_availability
- book_calendar_appointment
- reschedule_appointment
- update_contact_info
- move_lead_stage
- add_lead_tag
- add_internal_note
- escalate_to_human
- stay_silent

Only tools explicitly declared in the agent's instructions (via [Herramienta: name])
are instantiated and exposed to the model.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import unicodedata
import uuid
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import (
    Agent, Appointment, Client, Contact, Conversation, Message,
    PipelineStage, Professional, Service, now_utc
)
from ...schemas_appointments import AppointmentCreate, AppointmentUpdate
from ..appointments import (
    calculate_availability, create_appointment, get_client_timezone, update_appointment
)
from ..conversation_state import record_activity, set_pipeline_stage
from ..escalation import EscalationRequest
from ..pipeline import list_stages
from ..tags import create_tag
from .specs import ToolSpec

DAYS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MONTHS_ES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
]


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower().strip()


@dataclass
class CommercialEffects:
    notes: list[str] = field(default_factory=list)
    escalation: list[EscalationRequest] = field(default_factory=list)
    pipeline_stage: list[PipelineStage] = field(default_factory=list)
    is_silent: bool = False
    new_contact_name: str | None = None


def build_commercial_tools(
    db: Session,
    client: Client,
    conversation: Conversation,
    agent: Agent,
    contact: Contact | None,
    declared_names: list[str],
    effects: CommercialEffects,
) -> list[ToolSpec]:
    """Return tool specs only for the tools cited in declared_names."""
    specs: list[ToolSpec] = []
    declared_set = set(declared_names)
    tz = get_client_timezone(client)

    # 1. check_calendar_availability
    if "check_calendar_availability" in declared_set:
        def check_availability_handler(args: dict) -> tuple[str, bool]:
            start_date_str = (args.get("startDate") or args.get("start_date") or "").strip()
            if not start_date_str:
                now_local = datetime.now(tz)
                date_from = now_local.date()
            else:
                try:
                    date_from = date.fromisoformat(start_date_str)
                except ValueError:
                    return f"Error: formato de fecha inválido '{start_date_str}'. Usa YYYY-MM-DD.", True

            date_to = date_from + timedelta(days=6)
            prof_arg = (args.get("professional") or args.get("professional_id") or "").strip()
            service_arg = (args.get("service_id") or "").strip()

            target_prof_id: uuid.UUID | None = None
            target_service_id: uuid.UUID | None = None

            if service_arg:
                try:
                    target_service_id = uuid.UUID(service_arg)
                except ValueError:
                    target_service_id = None

            if prof_arg:
                # Check if prof_arg matches a professional by UUID or name/role
                matched_prof = None
                try:
                    prof_uuid = uuid.UUID(prof_arg)
                    matched_prof = db.scalar(select(Professional).where(Professional.id == prof_uuid, Professional.client_id == client.id))
                except ValueError:
                    pass

                if not matched_prof:
                    clean_prof = strip_accents(prof_arg)
                    for p in (client.professionals or []):
                        if p.is_active and (clean_prof in strip_accents(p.name) or (p.role and clean_prof in strip_accents(p.role))):
                            matched_prof = p
                            break

                if matched_prof:
                    target_prof_id = matched_prof.id
                elif not target_service_id:
                    # Check if prof_arg matches a service (e.g. "Ortodoncia")
                    clean_prof = strip_accents(prof_arg)
                    for s in (client.services or []):
                        if s.is_active and clean_prof in strip_accents(s.name):
                            target_service_id = s.id
                            break

            days_data = calculate_availability(
                db, client, date_from, date_to,
                service_id=target_service_id,
                professional_id=target_prof_id,
            )

            # Check if today is requested and has no slots
            now_local_date = datetime.now(tz).date()
            first_day = days_data[0] if days_data else None
            is_first_day_today = first_day and first_day["date"] == now_local_date.isoformat()

            days_with_slots = [d for d in days_data if d["slots"]]
            if not days_with_slots:
                if is_first_day_today and (not first_day or not first_day["slots"]):
                    return "Sin cupos disponibles para hoy (agenda completa para la fecha consultada).", False
                return f"No hay cupos disponibles entre el {date_from.isoformat()} y el {date_to.isoformat()}.", False

            lines = ["Disponibilidad de horarios encontrados:"]
            for d in days_with_slots[:4]:
                d_obj = date.fromisoformat(d["date"])
                d_name = DAYS_ES[d_obj.weekday()]
                slot_times = []
                for s in d["slots"][:6]:
                    st_local = s["start_time"].astimezone(tz)
                    slot_times.append(st_local.strftime("%H:%M"))
                times_str = ", ".join(slot_times)
                lines.append(f"• {d_name} {d_obj.day} de {MONTHS_ES[d_obj.month]}: {times_str} hrs")

            return "\n".join(lines), False

        specs.append(
            ToolSpec(
                name="check_calendar_availability",
                description="Consulta los días y horas disponibles en la agenda médica de la clínica.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "startDate": {"type": "string", "description": "Fecha inicial a consultar en formato estricto YYYY-MM-DD"},
                        "professional": {"type": "string", "description": "Especialidad o área clínica (ej: 'Ortodoncia') o ID de profesional opcional"},
                        "service_id": {"type": "string", "description": "ID del servicio opcional"},
                    },
                    "required": ["startDate"],
                },
                handler=check_availability_handler,
            )
        )

    # 2. book_calendar_appointment
    if "book_calendar_appointment" in declared_set:
        def book_appointment_handler(args: dict) -> tuple[str, bool]:
            nonlocal contact
            if not contact and client is not None:
                contact = Contact(
                    client_id=client.id,
                    name=getattr(conversation, "contact_name", "") or "",
                    phone="+56900000000",
                )
                db.add(contact)
                db.flush()
                conversation.contact_id = contact.id
                db.flush()

            # Guardrail: real name required
            if not contact or not contact.name or not contact.name.strip():
                return "Error: No se puede agendar la cita sin el nombre real del paciente registrado. Solicita el nombre y apellido al paciente primero.", True

            start_str = (args.get("start_time") or args.get("hora") or "").strip()
            title = (args.get("title") or "Evaluación Dental").strip()

            if not start_str:
                return "Error: debes proporcionar la fecha y hora de inicio en formato ISO 8601.", True

            try:
                # Handle possible malformed offset or naive datetime
                raw_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                # Interpret as wall-clock in client timezone
                slot_start = datetime(
                    raw_dt.year, raw_dt.month, raw_dt.day,
                    raw_dt.hour, raw_dt.minute, raw_dt.second,
                    tzinfo=tz
                )
            except Exception as e:
                return f"Error al interpretar la fecha y hora '{start_str}': {str(e)}. Usa formato ISO 8601.", True

            # Deduce service
            target_service: Service | None = None
            service_id_arg = args.get("service_id")
            if service_id_arg:
                try:
                    s_uuid = uuid.UUID(service_id_arg)
                    target_service = db.scalar(select(Service).where(Service.id == s_uuid, Service.client_id == client.id))
                except ValueError:
                    pass

            if not target_service:
                clean_title = strip_accents(title)
                for s in (client.services or []):
                    if s.is_active and strip_accents(s.name) in clean_title:
                        target_service = s
                        break
                if not target_service:
                    # Default to first active service or eval
                    for s in (client.services or []):
                        if s.is_active and "evaluacion" in strip_accents(s.name):
                            target_service = s
                            break

            # Find professional
            target_prof: Professional | None = None
            prof_id_arg = args.get("professional_id")
            if prof_id_arg:
                try:
                    p_uuid = uuid.UUID(prof_id_arg)
                    target_prof = db.scalar(select(Professional).where(Professional.id == p_uuid, Professional.client_id == client.id))
                except ValueError:
                    pass

            if not target_prof and target_service:
                # Find professional offering this service
                for p in (client.professionals or []):
                    if p.is_active and any(s.id == target_service.id for s in p.services):
                        target_prof = p
                        break
            if not target_prof:
                # Pick any active professional
                for p in (client.professionals or []):
                    if p.is_active:
                        target_prof = p
                        break

            duration = target_service.duration_minutes if target_service else 30
            create_payload = AppointmentCreate(
                conversation_id=conversation.id,
                contact_id=contact.id,
                professional_id=target_prof.id if target_prof else None,
                service_id=target_service.id if target_service else None,
                title=title,
                start_time=slot_start,
                duration_minutes=duration,
                notes=f"Agendado automáticamente por asistente IA ({agent.name})",
            )

            try:
                appointment = create_appointment(db, client, create_payload, actor_name=agent.name)
            except Exception as exc:
                return f"No se pudo reservar el cupo solicitado: {str(exc)}", True

            day_name = DAYS_ES[slot_start.weekday()]
            month_name = MONTHS_ES[slot_start.month]
            time_formatted = slot_start.strftime("%H:%M")
            return f"Cita confirmada y agendada exitosamente: '{appointment.title}' para el {day_name} {slot_start.day} de {month_name} a las {time_formatted} hrs.", False

        specs.append(
            ToolSpec(
                name="book_calendar_appointment",
                description="Reserva formalmente una hora de atención dental en la agenda. Solo debe llamarse tras la confirmación afirmativa del paciente.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "start_time": {"type": "string", "description": "Fecha y hora de inicio de la cita en formato ISO 8601 (ej: '2026-09-28T16:00:00-03:00')"},
                        "title": {"type": "string", "description": "Título o motivo de la cita (ej: 'Evaluación Dental Inicial - Ortodoncia')"},
                        "taskType": {"type": "string", "description": "Tipo de tarea o cita (ej: 'meeting')"},
                        "service_id": {"type": "string", "description": "ID del servicio opcional"},
                    },
                    "required": ["start_time", "title"],
                },
                handler=book_appointment_handler,
            )
        )

    # 3. reschedule_appointment
    if "reschedule_appointment" in declared_set:
        def reschedule_appointment_handler(args: dict) -> tuple[str, bool]:
            if not contact:
                return "Error: no hay contacto asociado a esta conversación.", True

            num = args.get("appointment_number")
            apt_id_str = args.get("appointment_id")
            new_start_str = (args.get("new_start_time") or "").strip()

            if not new_start_str:
                return "Error: debes proporcionar la nueva fecha y hora (new_start_time).", True

            try:
                raw_dt = datetime.fromisoformat(new_start_str.replace("Z", "+00:00"))
                new_start = datetime(
                    raw_dt.year, raw_dt.month, raw_dt.day,
                    raw_dt.hour, raw_dt.minute, raw_dt.second,
                    tzinfo=tz
                )
            except Exception as e:
                return f"Error al interpretar la nueva fecha y hora: {str(e)}", True

            # Find target appointment
            target_apt = None
            if num is not None:
                try:
                    idx = int(num) - 1
                    active_apts = db.scalars(
                        select(Appointment)
                        .where(
                            Appointment.client_id == client.id,
                            Appointment.contact_id == contact.id,
                            Appointment.status == "confirmed",
                            Appointment.start_time > now_utc(),
                        )
                        .order_by(Appointment.start_time.asc())
                    ).all()
                    if 0 <= idx < len(active_apts):
                        target_apt = active_apts[idx]
                except (ValueError, TypeError):
                    pass

            if not target_apt and apt_id_str:
                try:
                    apt_uuid = uuid.UUID(apt_id_str)
                    target_apt = db.scalar(select(Appointment).where(Appointment.id == apt_uuid, Appointment.client_id == client.id))
                except ValueError:
                    pass

            if not target_apt:
                # Default to first active future appointment
                target_apt = db.scalar(
                    select(Appointment)
                    .where(
                        Appointment.client_id == client.id,
                        Appointment.contact_id == contact.id,
                        Appointment.status == "confirmed",
                        Appointment.start_time > now_utc(),
                    )
                    .order_by(Appointment.start_time.asc())
                    .limit(1)
                )

            if not target_apt:
                return "Error: no se encontró ninguna cita activa para reprogramar.", True

            try:
                updated = update_appointment(
                    db, client, target_apt.id,
                    AppointmentUpdate(start_time=new_start),
                    actor_name=agent.name
                )
            except Exception as exc:
                return f"No se pudo reprogramar la cita: {str(exc)}", True

            day_name = DAYS_ES[new_start.weekday()]
            month_name = MONTHS_ES[new_start.month]
            time_formatted = new_start.strftime("%H:%M")
            return f"Cita '{updated.title}' reprogramada exitosamente para el {day_name} {new_start.day} de {month_name} a las {time_formatted} hrs.", False

        specs.append(
            ToolSpec(
                name="reschedule_appointment",
                description="Reprograma una cita existente del paciente a un nuevo día u horario acordado.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "new_start_time": {"type": "string", "description": "Nueva fecha y hora en formato ISO 8601"},
                        "appointment_number": {"type": "integer", "description": "Número de la cita según la lista de citas activas (1, 2...)"},
                        "reason": {"type": "string", "description": "Motivo del cambio de hora"},
                    },
                    "required": ["new_start_time"],
                },
                handler=reschedule_appointment_handler,
            )
        )

    # 4. update_contact_info
    if "update_contact_info" in declared_set or "update_lead_fields" in declared_set:
        def update_contact_info_handler(args: dict) -> tuple[str, bool]:
            nonlocal contact
            if not contact:
                if client is not None:
                    contact = Contact(
                        client_id=client.id,
                        name="",
                        phone="+56900000000",
                    )
                    db.add(contact)
                    db.flush()
                    conversation.contact_id = contact.id
                    db.flush()
                else:
                    return "Error: no hay contacto asociado a esta conversación.", True

            name = (args.get("name") or "").strip()
            phone = (args.get("phone") or "").strip()
            email = (args.get("email") or "").strip()

            updated_fields = []
            if name:
                contact.name = name
                conversation.contact_name = name
                effects.new_contact_name = name
                updated_fields.append(f"Nombre: {name}")

            if phone:
                contact.phone = phone
                updated_fields.append(f"Teléfono: {phone}")

            if email:
                contact.email = email
                updated_fields.append(f"Email: {email}")

            if updated_fields:
                record_activity(
                    db, conversation, "contact_updated",
                    actor=agent.name,
                    details={"fields": ", ".join(updated_fields)}
                )
                return f"Ficha de contacto actualizada exitosamente ({', '.join(updated_fields)}).", False

            return "No se especificaron datos para actualizar.", False

        name_to_expose = "update_contact_info" if "update_contact_info" in declared_set else "update_lead_fields"
        specs.append(
            ToolSpec(
                name=name_to_expose,
                description="Actualiza el nombre, teléfono o email del paciente en su ficha de contacto en el CRM.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Nombre completo del paciente (ej: 'Camila Soto')"},
                        "phone": {"type": "string", "description": "Número telefónico"},
                        "email": {"type": "string", "description": "Correo electrónico"},
                    },
                },
                handler=update_contact_info_handler,
            )
        )

    # 5. move_lead_stage
    if "move_lead_stage" in declared_set or "move_pipeline_stage" in declared_set:
        def move_stage_handler(args: dict) -> tuple[str, bool]:
            target_name = (args.get("stage_name") or args.get("stage") or "").strip()
            if not target_name:
                return "Error: debes indicar el nombre de la etapa destino.", True

            stages = list_stages(db, client)
            clean_target = strip_accents(target_name)

            matched_stage: PipelineStage | None = None
            for s in stages:
                if strip_accents(s.name) == clean_target:
                    matched_stage = s
                    break

            if not matched_stage:
                for s in stages:
                    if clean_target in strip_accents(s.name):
                        matched_stage = s
                        break

            if not matched_stage:
                valid = [s.name for s in stages]
                return f"Etapa no encontrada '{target_name}'. Etapas válidas: {', '.join(valid)}", True

            # Anti-regression check: do not downgrade if lead is already in a later stage
            current_stage = conversation.pipeline_stage
            if current_stage and matched_stage.position < current_stage.position:
                return (
                    f"Etapa comercial mantenida en '{current_stage.name}' (el lead ya se encuentra en una etapa más avanzada; no se retrocede a '{matched_stage.name}').",
                    False
                )

            effects.pipeline_stage.clear()
            effects.pipeline_stage.append(matched_stage)
            return f"Lead movido a la etapa comercial '{matched_stage.name}'.", False

        tool_name = "move_lead_stage" if "move_lead_stage" in declared_set else "move_pipeline_stage"
        specs.append(
            ToolSpec(
                name=tool_name,
                description="Mueve al prospecto/lead a una etapa del embudo comercial (ej: 'Descubrimiento', 'Cita Agendada').",
                input_schema={
                    "type": "object",
                    "properties": {
                        "stage_name": {"type": "string", "description": "Nombre de la etapa destino del embudo"},
                    },
                    "required": ["stage_name"],
                },
                handler=move_stage_handler,
            )
        )

    # 6. add_lead_tag
    if "add_lead_tag" in declared_set:
        def add_tag_handler(args: dict) -> tuple[str, bool]:
            nonlocal contact
            if not contact and client is not None:
                contact = Contact(
                    client_id=client.id,
                    name=getattr(conversation, "contact_name", "") or "",
                    phone="+56900000000",
                )
                db.add(contact)
                db.flush()
                conversation.contact_id = contact.id
                db.flush()

            if not contact:
                return "Error: no hay contacto asociado a esta conversación.", True

            tag_name = (args.get("tag") or args.get("name") or "").strip()
            if not tag_name:
                return "Error: debes indicar el nombre de la etiqueta.", True

            clean_tag = strip_accents(tag_name)
            # Find existing tag
            matched_tag = None
            for t in (client.tags or []):
                if strip_accents(t.name) == clean_tag:
                    matched_tag = t
                    break

            if not matched_tag:
                # Create tag
                matched_tag = create_tag(db, client, name=tag_name, color=None)

            if matched_tag not in contact.tags:
                contact.tags.append(matched_tag)
                record_activity(
                    db, conversation, "tag_added",
                    actor=agent.name,
                    details={"tag": matched_tag.name}
                )

            return f"Etiqueta '{matched_tag.name}' asignada exitosamente al lead.", False

        specs.append(
            ToolSpec(
                name="add_lead_tag",
                description="Asigna una etiqueta descriptiva al lead o contacto (ej: 'Ortodoncia', 'Carillas', 'Urgencia Dolor').",
                input_schema={
                    "type": "object",
                    "properties": {
                        "tag": {"type": "string", "description": "Nombre de la etiqueta a asignar"},
                    },
                    "required": ["tag"],
                },
                handler=add_tag_handler,
            )
        )

    # 7. add_internal_note
    if "add_internal_note" in declared_set or "add_lead_note" in declared_set:
        def add_note_handler(args: dict) -> tuple[str, bool]:
            content = (args.get("content") or args.get("note") or "").strip()
            if not content:
                return "Error: el contenido de la nota no puede estar vacío.", True

            effects.notes.append(content)
            return "Nota interna privada registrada exitosamente para el equipo.", False

        note_tool_name = "add_internal_note" if "add_internal_note" in declared_set else "add_lead_note"
        specs.append(
            ToolSpec(
                name=note_tool_name,
                description="Registra una nota interna privada en el CRM (visible solo para el equipo humano de la clínica).",
                input_schema={
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "Texto detallado de la observación o nota interna"},
                    },
                    "required": ["content"],
                },
                handler=add_note_handler,
            )
        )

    # 8. escalate_to_human
    if "escalate_to_human" in declared_set or "transfer_to_human" in declared_set:
        def escalate_handler(args: dict) -> tuple[str, bool]:
            reason = (args.get("reason") or args.get("motivo") or "").strip() or "Derivado por asistente IA"
            effects.escalation.clear()
            effects.escalation.append(EscalationRequest(reason=reason))
            return "Escalamiento registrado; un operador humano continuará la conversación.", False

        esc_name = "escalate_to_human" if "escalate_to_human" in declared_set else "transfer_to_human"
        specs.append(
            ToolSpec(
                name=esc_name,
                description="Transfiere la conversación a una persona del equipo humano o recepción.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string", "description": "Motivo del escalamiento"},
                    },
                },
                handler=escalate_handler,
            )
        )

    # 9. stay_silent
    if "stay_silent" in declared_set:
        def stay_silent_handler(args: dict) -> tuple[str, bool]:
            effects.is_silent = True
            return "[SILENCIO registrado: se omite el mensaje saliente al chat]", False

        specs.append(
            ToolSpec(
                name="stay_silent",
                description="Suprime la respuesta automática al cliente cuando una situación amerita no enviar mensaje.",
                input_schema={"type": "object", "properties": {}},
                handler=stay_silent_handler,
            )
        )

    return specs


def apply_commercial_effects(
    db: Session,
    conversation: Conversation,
    agent: Agent,
    effects: CommercialEffects,
) -> None:
    """Apply queued mutations after the completion reply is generated."""
    # 1. Notes
    for content in effects.notes:
        note = Message(
            conversation_id=conversation.id,
            role="assistant",
            kind="note",
            content=content,
            sender_type="ai",
            sender_name=agent.name,
        )
        db.add(note)

    # 2. Pipeline stage
    if effects.pipeline_stage:
        target_stage = effects.pipeline_stage[-1]
        set_pipeline_stage(db, conversation, target_stage, actor=agent.name)
