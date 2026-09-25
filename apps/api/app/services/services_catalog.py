"""Services: products and services a client's business offers.

Shared by the client portal and the agency's client page, which manage the
same rows from two doors. Both routers resolve the client their own way and
hand it here; every query is confined to that client, and the client itself
was already resolved inside the caller's agency.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Client, Service
from ..schemas_services import ServiceCreate, ServiceUpdate

MAX_SERVICES = 200


def list_services(db: Session, client: Client, active_only: bool = False) -> list[Service]:
    query = select(Service).where(Service.client_id == client.id)
    if active_only:
        query = query.where(Service.is_active.is_(True))
    return list(db.scalars(query.order_by(Service.position, Service.created_at, Service.id)))


def get_service(db: Session, client: Client, service_id: uuid.UUID) -> Service:
    row = db.scalar(select(Service).where(Service.id == service_id, Service.client_id == client.id))
    if not row:
        raise HTTPException(status_code=404, detail="Service not found")
    return row


def create_service(db: Session, client: Client, payload: ServiceCreate) -> Service:
    existing = list_services(db, client)
    if len(existing) >= MAX_SERVICES:
        raise HTTPException(status_code=409, detail=f"A client can have up to {MAX_SERVICES} services")
    position = payload.position if payload.position > 0 else len(existing)
    deposit_amount = payload.deposit_amount
    if payload.requires_deposit and deposit_amount is not None and payload.price > 0 and deposit_amount > payload.price:
        raise HTTPException(status_code=422, detail="Deposit amount cannot exceed the service price")
    row = Service(
        agency_id=client.agency_id,
        client_id=client.id,
        name=payload.name,
        description=payload.description.strip(),
        price=payload.price,
        currency=payload.currency or client.currency or "USD",
        duration_minutes=payload.duration_minutes,
        modality=payload.modality,
        requires_deposit=payload.requires_deposit,
        deposit_amount=deposit_amount if payload.requires_deposit else None,
        requirements=payload.requirements.strip(),
        is_active=payload.is_active,
        position=position,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_service(db: Session, client: Client, service_id: uuid.UUID, payload: ServiceUpdate) -> Service:
    row = get_service(db, client, service_id)
    values = payload.model_dump(exclude_unset=True)
    for key in (
        "name",
        "description",
        "price",
        "currency",
        "duration_minutes",
        "modality",
        "requires_deposit",
        "deposit_amount",
        "requirements",
        "is_active",
        "position",
    ):
        if key in values:
            setattr(row, key, values[key])
    if not row.requires_deposit:
        row.deposit_amount = None
    elif row.deposit_amount is not None and row.price > 0 and row.deposit_amount > row.price:
        raise HTTPException(status_code=422, detail="Deposit amount cannot exceed the service price")
    db.commit()
    db.refresh(row)
    return row


def delete_service(db: Session, client: Client, service_id: uuid.UUID) -> None:
    row = get_service(db, client, service_id)
    db.delete(row)
    db.commit()
