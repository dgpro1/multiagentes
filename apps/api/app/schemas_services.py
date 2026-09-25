"""Services: what the agency's client page and the client portal send and read."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schemas import check_currency

MODALITIES = ("presencial", "online", "a_domicilio")
ModalityType = Literal["presencial", "online", "a_domicilio"]


def check_modality(value: str | None) -> str:
    cleaned = (value or "").strip().lower()
    if cleaned not in MODALITIES:
        raise ValueError(f"Modality must be one of: {', '.join(MODALITIES)}")
    return cleaned


class ServiceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    description: str = Field(default="", max_length=2000)
    price: float = Field(default=0.0, ge=0.0)
    currency: str = Field(default="USD", max_length=3)
    duration_minutes: int = Field(default=30, ge=1, le=1440)
    modality: str = Field(default="presencial", max_length=32)
    requires_deposit: bool = False
    deposit_amount: float | None = Field(default=None, ge=0.0)
    requirements: str = Field(default="", max_length=2000)
    is_active: bool = True
    position: int = Field(default=0, ge=0)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A service needs a name")
        return value

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str | None) -> str:
        return check_currency(value)

    @field_validator("modality")
    @classmethod
    def _modality(cls, value: str | None) -> str:
        return check_modality(value)


class ServiceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = Field(default=None, max_length=2000)
    price: float | None = Field(default=None, ge=0.0)
    currency: str | None = Field(default=None, max_length=3)
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    modality: str | None = Field(default=None, max_length=32)
    requires_deposit: bool | None = None
    deposit_amount: float | None = Field(default=None, ge=0.0)
    requirements: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None
    position: int | None = Field(default=None, ge=0)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("A service needs a name")
        return value

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str | None) -> str | None:
        return None if value is None else check_currency(value)

    @field_validator("modality")
    @classmethod
    def _modality(cls, value: str | None) -> str | None:
        return None if value is None else check_modality(value)


class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_id: uuid.UUID
    name: str
    description: str
    price: float
    currency: str
    duration_minutes: int
    modality: str
    requires_deposit: bool
    deposit_amount: float | None
    requirements: str
    is_active: bool
    position: int
    created_at: datetime
    updated_at: datetime
