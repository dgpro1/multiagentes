import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CalendarMemberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(default="", max_length=120)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class CalendarMemberUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: str | None = Field(default=None, max_length=120)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class CalendarMemberOut(BaseModel):
    id: uuid.UUID
    name: str
    role: str
    color: str
    status: str
    google_email: str | None = None
    connected_at: datetime | None = None
    last_error: str | None = None
    connect_url: str
    connect_expires_at: datetime
    link_expired: bool


class CalendarOverviewOut(BaseModel):
    oauth_ready: bool
    timezone: str
    members: list[CalendarMemberOut]


class CalendarEventOut(BaseModel):
    id: str
    member_id: uuid.UUID
    title: str
    start: str
    end: str
    all_day: bool
    location: str = ""
    url: str = ""


class CalendarEventError(BaseModel):
    member_id: uuid.UUID
    detail: str


class CalendarEventsOut(BaseModel):
    events: list[CalendarEventOut]
    errors: list[CalendarEventError]


class CalendarConnectInfoOut(BaseModel):
    member_name: str
    member_role: str
    color: str
    client_name: str
    has_logo: bool
    status: str
    google_email: str | None = None
    oauth_ready: bool
    expired: bool


class CalendarConnectStartOut(BaseModel):
    authorization_url: str
