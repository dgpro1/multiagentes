"""Scheduled messages: schemas for creating, updating, and returning scheduled messages."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ScheduledMessageStatus = Literal["pending", "sent", "cancelled", "failed"]


class ScheduledMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10000)
    scheduled_for: datetime
    via_conversation_id: uuid.UUID | None = None

    @field_validator("content")
    @classmethod
    def _clean_content(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Message content cannot be blank")
        return s


class ScheduledMessageUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=10000)
    scheduled_for: datetime | None = None

    @field_validator("content")
    @classmethod
    def _clean_content(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        if not s:
            raise ValueError("Message content cannot be blank")
        return s


class ScheduledMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agency_id: uuid.UUID
    client_id: uuid.UUID
    conversation_id: uuid.UUID
    via_conversation_id: uuid.UUID | None = None
    portal_user_id: uuid.UUID | None = None
    sender_type: str
    sender_name: str | None = None
    content: str
    scheduled_for: datetime
    status: ScheduledMessageStatus
    failure_reason: str | None = None
    sent_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
