"""Professionals: the people a client's business books time with.

Shared by the client portal and the agency's client page, which manage the
same rows from two doors. Both routers resolve the client their own way and
hand it here; every query is confined to that client, and the client itself
was already resolved inside the caller's agency.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Client, Professional, Service
from ..schemas_professionals import ProfessionalCreate, ProfessionalUpdate
from .calendar import PALETTE

MAX_PROFESSIONALS = 50


def list_professionals(db: Session, client: Client) -> list[Professional]:
    return list(
        db.scalars(
            select(Professional).where(Professional.client_id == client.id).order_by(Professional.created_at, Professional.id)
        )
    )


def get_professional(db: Session, client: Client, professional_id: uuid.UUID) -> Professional:
    row = db.scalar(select(Professional).where(Professional.id == professional_id, Professional.client_id == client.id))
    if not row:
        raise HTTPException(status_code=404, detail="Professional not found")
    return row


def create_professional(db: Session, client: Client, payload: ProfessionalCreate) -> Professional:
    existing = list_professionals(db, client)
    if len(existing) >= MAX_PROFESSIONALS:
        raise HTTPException(status_code=409, detail=f"A client can have up to {MAX_PROFESSIONALS} professionals")
    taken = {row.color for row in existing}
    color = payload.color or next((c for c in PALETTE if c not in taken), PALETTE[len(existing) % len(PALETTE)])
    row = Professional(
        agency_id=client.agency_id,
        client_id=client.id,
        name=payload.name,
        role=payload.role.strip(),
        color=color,
        is_active=payload.is_active,
        slot_minutes=payload.slot_minutes,
        weekly_hours=payload.weekly_hours,
    )
    if payload.service_ids:
        services = list(
            db.scalars(
                select(Service).where(
                    Service.id.in_(payload.service_ids),
                    Service.client_id == client.id,
                )
            ).all()
        )
        row.services = services
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_professional(db: Session, client: Client, professional_id: uuid.UUID, payload: ProfessionalUpdate) -> Professional:
    row = get_professional(db, client, professional_id)
    values = payload.model_dump(exclude_unset=True)
    for key in ("name", "color", "is_active", "slot_minutes", "weekly_hours"):
        # An explicit null is not a value for a column that cannot hold one.
        if values.get(key) is not None:
            setattr(row, key, values[key])
    if values.get("role") is not None:
        row.role = values["role"].strip()
    if payload.service_ids is not None:
        services = list(
            db.scalars(
                select(Service).where(
                    Service.id.in_(payload.service_ids),
                    Service.client_id == client.id,
                )
            ).all()
        ) if payload.service_ids else []
        row.services = services
    db.commit()
    db.refresh(row)
    return row


def delete_professional(db: Session, client: Client, professional_id: uuid.UUID) -> None:
    row = get_professional(db, client, professional_id)
    db.delete(row)
    db.commit()
