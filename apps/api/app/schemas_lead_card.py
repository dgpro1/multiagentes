"""The lead card of the inbox: what the client portal and the agency's panel send and read."""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

FIELD_TYPES = ("text", "number", "date", "select", "checkbox")
FieldType = Literal["text", "number", "date", "select", "checkbox"]


def _label(value: str) -> str:
    label = value.strip()
    if not label:
        raise ValueError("The field needs a name")
    return label


class LeadFieldCreate(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    type: FieldType
    # Only a select has options; the service refuses them on any other type.
    options: list[str] = Field(default_factory=list, max_length=30)
    position: int | None = Field(default=None, ge=0)

    @field_validator("label")
    @classmethod
    def _named(cls, value: str) -> str:
        return _label(value)


class LeadFieldUpdate(BaseModel):
    """The key and the type never change: conversations store their values under them."""

    label: str | None = Field(default=None, min_length=1, max_length=80)
    options: list[str] | None = Field(default=None, max_length=30)
    position: int | None = Field(default=None, ge=0)

    @field_validator("label")
    @classmethod
    def _named(cls, value: str | None) -> str | None:
        return None if value is None else _label(value)


class LeadFieldOut(BaseModel):
    id: uuid.UUID
    key: str
    label: str
    type: str
    options: list[str] = Field(default_factory=list)
    position: int = 0


class LeadUpdate(BaseModel):
    """A partial change of the lead card. ``responsible_id`` null clears the
    chosen member (the business's own responsible person shows again);
    ``custom_values`` is partial too, and a null value clears that key."""

    responsible_id: uuid.UUID | None = None
    custom_values: dict[str, Any] | None = None


class LeadStageOut(BaseModel):
    id: uuid.UUID
    name: str
    color: str


class LeadResponsibleOut(BaseModel):
    id: uuid.UUID | None = None
    name: str | None = None
    # True when nobody was chosen on the conversation and this is the client's
    # own responsible person (``owner_name``).
    is_default: bool = True


class LeadTagOut(BaseModel):
    id: uuid.UUID
    name: str
    color: str


class LeadContactOut(BaseModel):
    id: uuid.UUID | None = None
    name: str | None = None
    # The push name the person set on their own WhatsApp, as it arrived.
    whatsapp_name: str | None = None
    phone: str | None = None
    email: str | None = None
    company: str | None = None
    blocked: bool = False
    tags: list[LeadTagOut] = Field(default_factory=list)


class LeadCardOut(BaseModel):
    conversation_id: uuid.UUID
    number: int
    channel: str
    account_label: str | None = None
    stage: LeadStageOut | None = None
    deal_value: float | None = None
    currency: str = "USD"
    responsible: LeadResponsibleOut
    owner_name: str | None = None
    custom_values: dict[str, Any] = Field(default_factory=dict)
    fields: list[LeadFieldOut] = Field(default_factory=list)
    contact: LeadContactOut
