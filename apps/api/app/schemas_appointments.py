"""Appointments: schemas for creating, updating, reading appointments and calculating availability."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AppointmentStatus = Literal["confirmed", "cancelled", "completed", "no_show"]


class AppointmentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start_time: datetime
    end_time: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=1440)
    professional_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)
    created_by_role: str | None = Field(default="operator", max_length=50)

    @field_validator("title")
    @classmethod
    def _clean_title(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Title cannot be blank")
        return s


class AppointmentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=1440)
    professional_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = None
    status: AppointmentStatus | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("title")
    @classmethod
    def _clean_title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        if not s:
            raise ValueError("Title cannot be blank")
        return s


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agency_id: uuid.UUID
    client_id: uuid.UUID
    conversation_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    professional_id: uuid.UUID | None
    service_id: uuid.UUID | None
    title: str
    start_time: datetime
    end_time: datetime
    duration_minutes: int
    status: str
    notes: str | None
    created_by_role: str | None
    created_at: datetime
    updated_at: datetime

    professional_name: str | None = None
    service_name: str | None = None
    contact_name: str | None = None


class AvailabilitySlot(BaseModel):
    start_time: datetime
    end_time: datetime
    professional_id: uuid.UUID | None = None
    professional_name: str | None = None


class AvailabilityDay(BaseModel):
    date: str  # YYYY-MM-DD
    slots: list[AvailabilitySlot]


class AvailabilityResponse(BaseModel):
    days: list[AvailabilityDay]
