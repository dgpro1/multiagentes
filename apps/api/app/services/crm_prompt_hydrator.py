"""Hydrates commercial system prompts with declarative dynamic blocks.

Inspects agent instructions for declarative block citations:
  [FECHA Y HORA ACTUAL DEL NEGOCIO]
  [FICHA COMERCIAL DEL PROSPECTO / CLIENTE]
  [CATÁLOGO OFICIAL DE SERVICIOS Y TARIFAS]
  [UBICACIÓN Y DATOS DEL NEGOCIO]
  [CITAS ACTIVAS PROGRAMADAS PARA ESTE CLIENTE]
  [NOTAS E INTERVENCIONES PREVIAS DEL EQUIPO HUMANO]

Only blocks explicitly referenced by the prompt are built and injected,
respecting the principle: "the prompt is the configuration".
"""

from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Appointment, Client, Contact, Conversation, Message, Service, now_utc
from .appointments import get_client_timezone

DECLARATIVE_TOOL_RE = re.compile(r"\[Herramienta:\s*([a-zA-Z0-9_-]+)\]", re.IGNORECASE)

DAYS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MONTHS_ES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
]


def declared_tools(instructions: str | None) -> list[str]:
    """Return tool names cited via [Herramienta: name] in the instructions."""
    if not instructions:
        return []
    matches = DECLARATIVE_TOOL_RE.findall(instructions)
    seen: set[str] = set()
    result: list[str] = []
    for m in matches:
        clean = m.strip().lower()
        if clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def has_declarative_tools(instructions: str | None) -> bool:
    """Return True if the prompt specifies tools using the declarative bracket syntax."""
    return bool(declared_tools(instructions))


def format_currency_clp(amount: float | int | None) -> str:
    if amount is None or amount == 0:
        return "$0 (Sin costo)"
    try:
        val = int(amount)
        formatted = f"{val:,}".replace(",", ".")
        return f"${formatted}"
    except (ValueError, TypeError):
        return f"${amount}"


def build_temporal_block(client: Client) -> str:
    tz = get_client_timezone(client)
    now_local = datetime.now(tz)
    day_name = DAYS_ES[now_local.weekday()]
    month_name = MONTHS_ES[now_local.month]
    time_str = now_local.strftime("%H:%M")
    offset_str = now_local.strftime("%z")
    if len(offset_str) == 5:
        offset_formatted = f"UTC{offset_str[:3]}:{offset_str[3:]}"
    else:
        offset_formatted = f"UTC{offset_str}"

    lines = [
        "[FECHA Y HORA ACTUAL DEL NEGOCIO]",
        f"Fecha y hora actual: {day_name} {now_local.day} de {month_name} de {now_local.year}, {time_str} hrs (Zona horaria: {client.timezone or 'America/Santiago'}, {offset_formatted}).",
        "Referencia de calendario oficial (próximos 14 días):",
    ]

    for i in range(14):
        target = now_local + timedelta(days=i)
        t_day_name = DAYS_ES[target.weekday()]
        t_date_str = target.strftime("%Y-%m-%d")
        t_label = "Hoy" if i == 0 else ("Mañana" if i == 1 else t_day_name)
        lines.append(f"- {t_label} {target.day:02d}/{target.month:02d}: {t_date_str}")

    return "\n".join(lines)


def build_contact_card_block(contact: Contact | None, conversation: Conversation) -> str:
    lines = ["[FICHA COMERCIAL DEL PROSPECTO / CLIENTE]"]
    real_name = (contact.name or "").strip() if contact else ""
    profile_name = (contact.whatsapp_contact_name or "").strip() if contact else ""

    if real_name:
        lines.append(f"- Nombre del paciente: {real_name}")
    elif profile_name:
        lines.append(f"- Nombre de perfil (no verificado): {profile_name}")
        lines.append("- Nombre real: No registrado (solicitar nombre y apellido al paciente)")
    else:
        lines.append("- Nombre real: No registrado (solicitar nombre y apellido al paciente)")

    if contact and contact.phone:
        lines.append(f"- Teléfono: {contact.phone}")
    if contact and contact.email:
        lines.append(f"- Email: {contact.email}")

    stage_name = conversation.pipeline_stage.name if conversation.pipeline_stage else None
    if stage_name:
        lines.append(f"- Etapa actual en embudo: {stage_name}")

    tags = [t.name for t in (contact.tags or [])] if contact else []
    if tags:
        lines.append(f"- Etiquetas: {', '.join(tags)}")

    if contact and getattr(contact, "custom_fields", None):
        fields = contact.custom_fields
        if isinstance(fields, dict) and fields:
            f_parts = [f"{k}: {v}" for k, v in fields.items()]
            lines.append(f"- Datos adicionales: {', '.join(f_parts)}")

    return "\n".join(lines)


def build_catalog_block(client: Client) -> str:
    lines = ["[CATÁLOGO OFICIAL DE SERVICIOS Y TARIFAS]"]
    services: list[Service] = sorted(
        [s for s in (client.services or []) if s.is_active],
        key=lambda s: s.name.lower()
    )
    if not services:
        lines.append("No hay servicios específicos publicados en el catálogo. Toda evaluación inicial es sin costo.")
        return "\n".join(lines)

    for s in services:
        price_str = format_currency_clp(s.price)
        dur_str = f"{s.duration_minutes} min" if s.duration_minutes else "30 min"
        desc = f" ({s.description})" if s.description else ""
        lines.append(f"• {s.name}{desc} — {price_str} — Duración aprox: {dur_str}")

    return "\n".join(lines)


def build_business_info_block(client: Client) -> str:
    lines = ["[UBICACIÓN Y DATOS DEL NEGOCIO]"]
    if client.address:
        lines.append(f"- Dirección: {client.address.strip()}")
    if client.business_hours:
        b_hours = client.business_hours or {}
        h_lines = []
        day_keys = [
            ("monday", "Lunes"), ("tuesday", "Martes"), ("wednesday", "Miércoles"),
            ("thursday", "Jueves"), ("friday", "Viernes"), ("saturday", "Sábado"), ("sunday", "Domingo")
        ]
        for key, name in day_keys:
            ranges = b_hours.get(key)
            if ranges and isinstance(ranges, list) and len(ranges) > 0:
                parts = [f"{r[0]} a {r[1]}" for r in ranges if len(r) == 2]
                if parts:
                    h_lines.append(f"{name}: {', '.join(parts)} hrs")
                else:
                    h_lines.append(f"{name}: Cerrado")
            else:
                h_lines.append(f"{name}: Cerrado")
        if h_lines:
            lines.append("- Horarios de atención:\n  " + "\n  ".join(h_lines))
    return "\n".join(lines)


def build_active_appointments_block(db: Session, client: Client, contact: Contact | None) -> str:
    lines = ["[CITAS ACTIVAS PROGRAMADAS PARA ESTE CLIENTE]"]
    if not contact:
        lines.append("No registra citas activas programadas.")
        return "\n".join(lines)

    tz = get_client_timezone(client)
    now = now_utc()
    appointments = db.scalars(
        select(Appointment)
        .where(
            Appointment.client_id == client.id,
            Appointment.contact_id == contact.id,
            Appointment.status == "confirmed",
            Appointment.start_time > now,
        )
        .order_by(Appointment.start_time.asc())
    ).all()

    if not appointments:
        lines.append("No registra citas activas programadas en el sistema.")
        return "\n".join(lines)

    for idx, apt in enumerate(appointments, start=1):
        dt_local = apt.start_time.astimezone(tz)
        day_name = DAYS_ES[dt_local.weekday()]
        month_name = MONTHS_ES[dt_local.month]
        time_str = dt_local.strftime("%H:%M")
        title = apt.title or "Evaluación Dental"
        lines.append(f"{idx}. {day_name} {dt_local.day} de {month_name} a las {time_str} hrs 🕒 (Motivo: {title})")

    return "\n".join(lines)


def build_team_notes_block(db: Session, conversation: Conversation) -> str:
    lines = ["[NOTAS E INTERVENCIONES PREVIAS DEL EQUIPO HUMANO]"]
    notes = db.scalars(
        select(Message)
        .where(
            Message.conversation_id == conversation.id,
            Message.kind == "note",
        )
        .order_by(Message.created_at.asc())
    ).all()

    if not notes:
        lines.append("No registra notas internas previas para este lead.")
        return "\n".join(lines)

    tz = get_client_timezone(conversation.client) if conversation.client else ZoneInfo("UTC")
    for n in notes:
        local_time = n.created_at.astimezone(tz).strftime("%Y-%m-%d %H:%M")
        author = n.sender_name or ("IA" if n.sender_type == "ai" else "Equipo")
        lines.append(f"• [{local_time}] {author}: {n.content}")

    return "\n".join(lines)


def hydrate_commercial_prompt(
    db: Session,
    client: Client,
    conversation: Conversation,
    instructions: str,
    contact: Contact | None = None,
) -> str:
    """Inject only the dynamic blocks explicitly cited in instructions."""
    blocks_to_append: list[str] = []

    if "[FECHA Y HORA ACTUAL DEL NEGOCIO]" in instructions:
        blocks_to_append.append(build_temporal_block(client))

    if "[UBICACIÓN Y DATOS DEL NEGOCIO]" in instructions or "[DATOS DEL NEGOCIO]" in instructions:
        blocks_to_append.append(build_business_info_block(client))

    if "[CATÁLOGO OFICIAL DE SERVICIOS Y TARIFAS]" in instructions or "[CATÁLOGO OFICIAL]" in instructions:
        blocks_to_append.append(build_catalog_block(client))

    if "[FICHA COMERCIAL DEL PROSPECTO / CLIENTE]" in instructions or "[FICHA COMERCIAL]" in instructions:
        blocks_to_append.append(build_contact_card_block(contact, conversation))

    if "[CITAS ACTIVAS PROGRAMADAS PARA ESTE CLIENTE]" in instructions or "[CITAS ACTIVAS]" in instructions:
        blocks_to_append.append(build_active_appointments_block(db, client, contact))

    if "[NOTAS E INTERVENCIONES PREVIAS DEL EQUIPO HUMANO]" in instructions or "[NOTAS DEL EQUIPO]" in instructions:
        blocks_to_append.append(build_team_notes_block(db, conversation))

    if not blocks_to_append:
        return instructions

    separator = "\n\n" + "=" * 50 + "\n[DATOS DINÁMICOS DEL NEGOCIO Y CLIENTE]\n" + "=" * 50 + "\n\n"
    return instructions + separator + "\n\n".join(blocks_to_append)


from dataclasses import dataclass
from .tools.commercial_tools import CommercialEffects, build_commercial_tools
from .tools.specs import ToolSpec


@dataclass
class AgentContextResult:
    system_content: str
    extra_specs: list[ToolSpec]
    effects: CommercialEffects | None
    is_commercial: bool


def build_agent_context(
    db: Session,
    agent: Agent,
    conversation: Conversation,
    base_system_content: str,
    channel: str,
    *,
    contact: Contact | None = None,
) -> AgentContextResult:
    """Build unified agent prompt context and tool specs across channels."""
    if has_declarative_tools(agent.instructions):
        declared = declared_tools(agent.instructions)
        effects = CommercialEffects()
        client = agent.client or (conversation.client if hasattr(conversation, "client") else None)
        c = contact or getattr(conversation, "contact", None)
        if c is None and client is not None:
            if conversation.contact_id:
                c = db.get(Contact, conversation.contact_id)
            if c is None:
                c = Contact(
                    client_id=client.id,
                    name=getattr(conversation, "contact_name", "") or "",
                    phone="+56900000000",
                )
                db.add(c)
                db.flush()
                conversation.contact_id = c.id
                db.flush()
        specs = build_commercial_tools(db, client, conversation, agent, c, declared, effects)
        hydrated_content = hydrate_commercial_prompt(db, client, conversation, base_system_content, contact=c)
        return AgentContextResult(
            system_content=hydrated_content,
            extra_specs=specs,
            effects=effects,
            is_commercial=True,
        )

    return AgentContextResult(
        system_content=base_system_content,
        extra_specs=[],
        effects=None,
        is_commercial=False,
    )

