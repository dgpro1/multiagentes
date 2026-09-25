"""Appointments, agency side: CRUD and availability calculation for client appointments.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api_scopes import APPOINTMENTS_MANAGE, APPOINTMENTS_READ
from ..database import get_db
from ..deps import confined_client_id, get_current_user, require
from ..models import Client, User
from ..schemas_appointments import (
    AppointmentCreate,
    AppointmentOut,
    AppointmentUpdate,
    AvailabilityDay,
    AvailabilityResponse,
)
from ..services import appointments as appointments_service

router = APIRouter(tags=["Appointments"])


def _client(db: Session, user: User, client_id: uuid.UUID) -> Client:
    query = select(Client).where(Client.id == client_id, Client.agency_id == user.agency_id)
    if (only_client := confined_client_id(user)) is not None:
        query = query.where(Client.id == only_client)
    client = db.scalar(query)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


@router.get(
    "/clients/{client_id}/appointments",
    response_model=list[AppointmentOut],
    dependencies=[Depends(require(APPOINTMENTS_READ))],
)
def client_appointments(
    client_id: uuid.UUID,
    conversation_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    professional_id: uuid.UUID | None = None,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _client(db, user, client_id)
    return appointments_service.list_appointments(
        db,
        client,
        conversation_id=conversation_id,
        contact_id=contact_id,
        professional_id=professional_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )


@router.post(
    "/clients/{client_id}/appointments",
    response_model=AppointmentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(APPOINTMENTS_MANAGE))],
)
def client_create_appointment(
    client_id: uuid.UUID,
    payload: AppointmentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _client(db, user, client_id)
    appointment = appointments_service.create_appointment(
        db,
        client,
        payload,
        actor_name=getattr(user, "name", None) or "Operator",
    )
    # Populate extra display names
    out = AppointmentOut.model_validate(appointment)
    out.professional_name = appointment.professional.name if appointment.professional else None
    out.service_name = appointment.service.name if appointment.service else None
    out.contact_name = appointment.contact.name if appointment.contact else None
    return out


@router.patch(
    "/clients/{client_id}/appointments/{appointment_id}",
    response_model=AppointmentOut,
    dependencies=[Depends(require(APPOINTMENTS_MANAGE))],
)
def client_update_appointment(
    client_id: uuid.UUID,
    appointment_id: uuid.UUID,
    payload: AppointmentUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _client(db, user, client_id)
    appointment = appointments_service.update_appointment(
        db,
        client,
        appointment_id,
        payload,
        actor_name=getattr(user, "name", None) or "Operator",
    )
    out = AppointmentOut.model_validate(appointment)
    out.professional_name = appointment.professional.name if appointment.professional else None
    out.service_name = appointment.service.name if appointment.service else None
    out.contact_name = appointment.contact.name if appointment.contact else None
    return out


@router.delete(
    "/clients/{client_id}/appointments/{appointment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require(APPOINTMENTS_MANAGE))],
)
def client_delete_appointment(
    client_id: uuid.UUID,
    appointment_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _client(db, user, client_id)
    appointments_service.delete_appointment(
        db,
        client,
        appointment_id,
        actor_name=getattr(user, "name", None) or "Operator",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/clients/{client_id}/appointments/availability",
    response_model=AvailabilityResponse,
    dependencies=[Depends(require(APPOINTMENTS_READ))],
)
def client_appointments_availability(
    client_id: uuid.UUID,
    date_from: date = Query(...),
    date_to: date = Query(...),
    service_id: uuid.UUID | None = None,
    professional_id: uuid.UUID | None = None,
    duration_minutes: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _client(db, user, client_id)
    days = appointments_service.calculate_availability(
        db,
        client,
        date_from,
        date_to,
        service_id=service_id,
        professional_id=professional_id,
        duration_minutes=duration_minutes,
    )
    return AvailabilityResponse(days=[AvailabilityDay(**d) for d in days])
