"""Appointments and availability engine.

Provides slot calculation crossing business hours, professional weekly schedules,
and confirmed appointments, with collision control via atomic row locks.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Appointment, Client, Contact, Conversation, Professional, Service, now_utc
from ..schemas_appointments import AppointmentCreate, AppointmentUpdate
from .conversation_state import record_activity

DAY_MAP = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
_CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_minutes(value: str) -> int:
    if not isinstance(value, str) or not _CLOCK.match(value):
        return 0
    return int(value[:2]) * 60 + int(value[3:])


def get_client_timezone(client: Client) -> ZoneInfo:
    tz_str = (client.timezone or "").strip()
    if tz_str:
        try:
            return ZoneInfo(tz_str)
        except Exception:
            pass
    return ZoneInfo("UTC")


def calculate_availability(
    db: Session,
    client: Client,
    date_from: date,
    date_to: date,
    *,
    service_id: uuid.UUID | None = None,
    professional_id: uuid.UUID | None = None,
    duration_minutes: int | None = None,
) -> list[dict]:
    if date_to < date_from:
        raise HTTPException(status_code=400, detail="date_to cannot be earlier than date_from")
    if (date_to - date_from).days > 60:
        raise HTTPException(status_code=400, detail="Date range cannot exceed 60 days")

    effective_duration = duration_minutes or 30
    effective_buffer = 0

    if service_id is not None:
        service = db.scalar(
            select(Service).where(
                Service.id == service_id,
                Service.client_id == client.id,
                Service.is_active.is_(True),
            )
        )
        if service is None:
            raise HTTPException(status_code=404, detail="Service not found")
        effective_duration = service.duration_minutes
        effective_buffer = 0

    total_slot_needed = effective_duration + effective_buffer
    tz = get_client_timezone(client)
    now_in_tz = datetime.now(tz)

    # Resolve target professionals
    if professional_id is not None:
        prof = db.scalar(
            select(Professional).where(
                Professional.id == professional_id,
                Professional.client_id == client.id,
                Professional.is_active.is_(True),
            )
        )
        if prof is None:
            raise HTTPException(status_code=404, detail="Professional not found")
        # If service_id is provided and professional has assigned services, verify they can perform it
        if service_id is not None and prof.services:
            if not any(s.id == service_id for s in prof.services):
                target_profs = []
            else:
                target_profs = [prof]
        else:
            target_profs = [prof]
    else:
        all_active = list(
            db.scalars(
                select(Professional).where(
                    Professional.client_id == client.id,
                    Professional.is_active.is_(True),
                )
            ).all()
        )
        if service_id is not None:
            offering = [p for p in all_active if any(s.id == service_id for s in p.services)]
            target_profs = offering if offering else [p for p in all_active if not p.services]
        else:
            target_profs = all_active

    # Query all active appointments for this window in UTC
    range_start_dt = datetime(date_from.year, date_from.month, date_from.day, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    range_end_dt = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=tz).astimezone(timezone.utc)

    existing_appointments = list(
        db.scalars(
            select(Appointment).where(
                Appointment.client_id == client.id,
                Appointment.status.in_(["confirmed"]),
                Appointment.start_time < range_end_dt,
                Appointment.end_time > range_start_dt,
            )
        ).all()
    )

    days_out: list[dict] = []
    curr = date_from

    while curr <= date_to:
        day_key = DAY_MAP[curr.weekday()]
        day_slots: list[dict] = []

        if target_profs:
            for prof in target_profs:
                week = prof.weekly_hours or {}
                ranges = week.get(day_key) or []
                step = prof.slot_minutes or 30

                for pair in ranges:
                    if len(pair) != 2:
                        continue
                    start_min = parse_minutes(pair[0])
                    end_min = parse_minutes(pair[1])

                    slot_min = start_min
                    while slot_min + total_slot_needed <= end_min:
                        slot_start_local = datetime(
                            curr.year, curr.month, curr.day, slot_min // 60, slot_min % 60, tzinfo=tz
                        )
                        slot_end_local = slot_start_local + timedelta(minutes=effective_duration)

                        if slot_start_local > now_in_tz:
                            slot_start_utc = slot_start_local.astimezone(timezone.utc)
                            slot_end_utc = slot_end_local.astimezone(timezone.utc)

                            # Collision check against existing appointments
                            collision = any(
                                (a.professional_id == prof.id or a.professional_id is None)
                                and slot_start_utc < a.end_time
                                and slot_end_utc > a.start_time
                                for a in existing_appointments
                            )
                            if not collision:
                                day_slots.append(
                                    {
                                        "start_time": slot_start_utc,
                                        "end_time": slot_end_utc,
                                        "professional_id": prof.id,
                                        "professional_name": prof.name,
                                    }
                                )
                        slot_min += step

        # Fallback to client's business_hours if no slots found from professionals and booking general
        if not day_slots and professional_id is None and client.business_hours:
            b_hours = client.business_hours or {}
            ranges = b_hours.get(day_key) or []
            step = 30

            for pair in ranges:
                if len(pair) != 2:
                    continue
                start_min = parse_minutes(pair[0])
                end_min = parse_minutes(pair[1])

                slot_min = start_min
                while slot_min + total_slot_needed <= end_min:
                    slot_start_local = datetime(
                        curr.year, curr.month, curr.day, slot_min // 60, slot_min % 60, tzinfo=tz
                    )
                    slot_end_local = slot_start_local + timedelta(minutes=effective_duration)

                    if slot_start_local > now_in_tz:
                        slot_start_utc = slot_start_local.astimezone(timezone.utc)
                        slot_end_utc = slot_end_local.astimezone(timezone.utc)

                        collision = any(
                            slot_start_utc < a.end_time and slot_end_utc > a.start_time
                            for a in existing_appointments
                        )
                        if not collision:
                            day_slots.append(
                                {
                                    "start_time": slot_start_utc,
                                    "end_time": slot_end_utc,
                                    "professional_id": None,
                                    "professional_name": None,
                                }
                            )
                    slot_min += step

        day_slots.sort(key=lambda s: s["start_time"])
        days_out.append({"date": curr.isoformat(), "slots": day_slots})
        curr += timedelta(days=1)

    return days_out


def create_appointment(
    db: Session,
    client: Client,
    data: AppointmentCreate,
    *,
    actor_name: str | None = None,
) -> Appointment:
    # 1. Lock candidate professional or client row to serialize concurrent bookings
    if data.professional_id is not None:
        prof = db.scalar(
            select(Professional)
            .where(
                Professional.id == data.professional_id,
                Professional.client_id == client.id,
            )
            .with_for_update()
        )
        if prof is None:
            raise HTTPException(status_code=404, detail="Professional not found")
    else:
        db.execute(select(Client.id).where(Client.id == client.id).with_for_update())

    # 2. Check service if provided
    duration = data.duration_minutes
    title = data.title.strip()
    if data.service_id is not None:
        service = db.scalar(
            select(Service).where(
                Service.id == data.service_id,
                Service.client_id == client.id,
            )
        )
        if service is None:
            raise HTTPException(status_code=404, detail="Service not found")
        if duration is None:
            duration = service.duration_minutes
        if not title:
            title = service.name

    if duration is None:
        duration = 30

    start_time = data.start_time
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    else:
        start_time = start_time.astimezone(timezone.utc)

    if data.end_time is not None:
        end_time = data.end_time
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        else:
            end_time = end_time.astimezone(timezone.utc)
    else:
        end_time = start_time + timedelta(minutes=duration)

    if end_time <= start_time:
        raise HTTPException(status_code=400, detail="end_time must be after start_time")

    # 3. Collision check
    collision_filters = [
        Appointment.client_id == client.id,
        Appointment.status == "confirmed",
        Appointment.start_time < end_time,
        Appointment.end_time > start_time,
    ]
    if data.professional_id is not None:
        collision_filters.append(
            or_(
                Appointment.professional_id == data.professional_id,
                Appointment.professional_id.is_(None),
            )
        )

    overlap = db.scalar(select(Appointment.id).where(*collision_filters).limit(1))
    if overlap is not None:
        raise HTTPException(status_code=409, detail="Selected slot is no longer available")

    # 4. Conversation & Contact verification
    contact_id = data.contact_id
    conversation: Conversation | None = None
    if data.conversation_id is not None:
        conversation = db.scalar(
            select(Conversation).where(
                Conversation.id == data.conversation_id,
                Conversation.client_id == client.id,
            )
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if contact_id is None and conversation.contact_id is not None:
            contact_id = conversation.contact_id

    if contact_id is not None:
        contact = db.scalar(
            select(Contact).where(
                Contact.id == contact_id,
                Contact.client_id == client.id,
            )
        )
        if contact is None:
            raise HTTPException(status_code=404, detail="Contact not found")

    appointment = Appointment(
        agency_id=client.agency_id,
        client_id=client.id,
        conversation_id=data.conversation_id,
        contact_id=contact_id,
        professional_id=data.professional_id,
        service_id=data.service_id,
        title=title,
        start_time=start_time,
        end_time=end_time,
        duration_minutes=duration,
        status="confirmed",
        notes=data.notes,
        created_by_role=data.created_by_role or "operator",
    )
    db.add(appointment)
    db.flush()

    if conversation is not None:
        date_str = start_time.strftime("%Y-%m-%d %H:%M UTC")
        record_activity(
            db,
            conversation,
            "appointment_created",
            actor=actor_name or "Operator",
            details={"title": appointment.title, "date": date_str},
        )

    db.commit()
    db.refresh(appointment)
    return appointment


def update_appointment(
    db: Session,
    client: Client,
    appointment_id: uuid.UUID,
    data: AppointmentUpdate,
    *,
    actor_name: str | None = None,
) -> Appointment:
    appointment = db.scalar(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.client_id == client.id,
        )
    )
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found")

    target_prof_id = data.professional_id if data.professional_id is not None else appointment.professional_id
    target_start = data.start_time if data.start_time is not None else appointment.start_time
    if target_start.tzinfo is None:
        target_start = target_start.replace(tzinfo=timezone.utc)
    else:
        target_start = target_start.astimezone(timezone.utc)

    if data.end_time is not None:
        target_end = data.end_time
        if target_end.tzinfo is None:
            target_end = target_end.replace(tzinfo=timezone.utc)
        else:
            target_end = target_end.astimezone(timezone.utc)
    elif data.duration_minutes is not None:
        target_end = target_start + timedelta(minutes=data.duration_minutes)
    elif data.start_time is not None:
        target_end = target_start + timedelta(minutes=appointment.duration_minutes)
    else:
        target_end = appointment.end_time
        if target_end.tzinfo is None:
            target_end = target_end.replace(tzinfo=timezone.utc)
        else:
            target_end = target_end.astimezone(timezone.utc)

    if target_end <= target_start:
        raise HTTPException(status_code=400, detail="end_time must be after start_time")

    time_or_prof_changed = (
        data.start_time is not None
        or data.end_time is not None
        or data.duration_minutes is not None
        or (data.professional_id is not None and data.professional_id != appointment.professional_id)
    )

    if time_or_prof_changed:
        # Lock and check collision
        if target_prof_id is not None:
            prof = db.scalar(
                select(Professional)
                .where(Professional.id == target_prof_id, Professional.client_id == client.id)
                .with_for_update()
            )
            if prof is None:
                raise HTTPException(status_code=404, detail="Professional not found")
        else:
            db.execute(select(Client.id).where(Client.id == client.id).with_for_update())

        collision_filters = [
            Appointment.client_id == client.id,
            Appointment.id != appointment.id,
            Appointment.status == "confirmed",
            Appointment.start_time < target_end,
            Appointment.end_time > target_start,
        ]
        if target_prof_id is not None:
            collision_filters.append(
                or_(
                    Appointment.professional_id == target_prof_id,
                    Appointment.professional_id.is_(None),
                )
            )

        overlap = db.scalar(select(Appointment.id).where(*collision_filters).limit(1))
        if overlap is not None:
            raise HTTPException(status_code=409, detail="Selected slot is no longer available")

        appointment.start_time = target_start
        appointment.end_time = target_end
        appointment.duration_minutes = int((target_end - target_start).total_seconds() // 60)
        appointment.professional_id = target_prof_id

        if appointment.conversation_id is not None:
            conv = db.scalar(select(Conversation).where(Conversation.id == appointment.conversation_id))
            if conv:
                date_str = target_start.strftime("%Y-%m-%d %H:%M UTC")
                record_activity(
                    db,
                    conv,
                    "appointment_rescheduled",
                    actor=actor_name or "Operator",
                    details={"title": appointment.title, "date": date_str},
                )

    if data.title is not None:
        appointment.title = data.title.strip()
    if data.service_id is not None:
        srv = db.scalar(select(Service).where(Service.id == data.service_id, Service.client_id == client.id))
        if not srv:
            raise HTTPException(status_code=404, detail="Service not found")
        appointment.service_id = data.service_id
    if data.notes is not None:
        appointment.notes = data.notes

    if data.status is not None and data.status != appointment.status:
        prev_status = appointment.status
        appointment.status = data.status
        if data.status == "cancelled" and appointment.conversation_id is not None:
            conv = db.scalar(select(Conversation).where(Conversation.id == appointment.conversation_id))
            if conv:
                record_activity(
                    db,
                    conv,
                    "appointment_cancelled",
                    actor=actor_name or "Operator",
                    details={"title": appointment.title},
                )

    appointment.updated_at = now_utc()
    db.commit()
    db.refresh(appointment)
    return appointment


def delete_appointment(
    db: Session,
    client: Client,
    appointment_id: uuid.UUID,
    *,
    actor_name: str | None = None,
) -> None:
    appointment = db.scalar(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.client_id == client.id,
        )
    )
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found")

    if appointment.conversation_id is not None:
        conv = db.scalar(select(Conversation).where(Conversation.id == appointment.conversation_id))
        if conv:
            record_activity(
                db,
                conv,
                "appointment_cancelled",
                actor=actor_name or "Operator",
                details={"title": appointment.title},
            )

    db.delete(appointment)
    db.commit()


def list_appointments(
    db: Session,
    client: Client,
    *,
    conversation_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    professional_id: uuid.UUID | None = None,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[dict]:
    query = (
        select(Appointment)
        .where(Appointment.client_id == client.id)
        .order_by(Appointment.start_time.asc())
    )
    if conversation_id is not None:
        query = query.where(Appointment.conversation_id == conversation_id)
    if contact_id is not None:
        query = query.where(Appointment.contact_id == contact_id)
    if professional_id is not None:
        query = query.where(Appointment.professional_id == professional_id)
    if status is not None:
        query = query.where(Appointment.status == status)
    if date_from is not None:
        query = query.where(Appointment.end_time >= date_from)
    if date_to is not None:
        query = query.where(Appointment.start_time <= date_to)

    rows = list(db.scalars(query).all())
    enriched: list[dict] = []
    for r in rows:
        enriched.append(
            {
                "id": r.id,
                "agency_id": r.agency_id,
                "client_id": r.client_id,
                "conversation_id": r.conversation_id,
                "contact_id": r.contact_id,
                "professional_id": r.professional_id,
                "service_id": r.service_id,
                "title": r.title,
                "start_time": r.start_time,
                "end_time": r.end_time,
                "duration_minutes": r.duration_minutes,
                "status": r.status,
                "notes": r.notes,
                "created_by_role": r.created_by_role,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
                "professional_name": r.professional.name if r.professional else None,
                "service_name": r.service.name if r.service else None,
                "contact_name": r.contact.name if r.contact else None,
            }
        )
    return enriched
