"""Schemas of the platform surface (SUPERADMIN -> AGENCIES -> CLIENTS)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictBool


class PlatformAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    is_active: bool
    created_at: datetime


class PlatformLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class PlatformAgencyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    slug: str | None = Field(default=None, max_length=180)
    admin_name: str = Field(min_length=1, max_length=160)
    admin_email: EmailStr


class PlatformAgencyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=180)
    slug: str | None = Field(default=None, max_length=180)
    brand_color: str | None = None


class PlatformAgencyOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    brand_color: str
    created_at: datetime
    client_count: int
    agent_count: int
    access_status: str
    access_blocked_at: datetime | None
    access_block_reason: str
    features: dict[str, bool]
    plan: str


class PlatformAccessUpdate(BaseModel):
    status: Literal["active", "blocked"]
    reason: str | None = Field(default=None, max_length=500)


class PlatformFeatureEntry(BaseModel):
    key: str
    default: bool


class PlatformFeaturesOut(BaseModel):
    catalog: list[PlatformFeatureEntry]
    presets: dict[str, list[str]]


class PlatformFeaturesUpdate(BaseModel):
    features: dict[str, StrictBool]
    plan: str | None = Field(default=None, max_length=40)


class PlatformClientOut(BaseModel):
    id: uuid.UUID
    name: str
    portal_slug: str
    is_active: bool
    data_mode: str
    agent_count: int
    created_at: datetime


class PlatformInvitationCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=160)


class PlatformInvitationOut(BaseModel):
    id: uuid.UUID
    agency_id: uuid.UUID
    agency_name: str
    email: str
    name: str
    status: str
    expires_at: datetime
    created_at: datetime


class PlatformInvitationIssued(PlatformInvitationOut):
    token: str
    url: str


class PlatformAgencyCreated(BaseModel):
    agency: PlatformAgencyOut
    invitation: PlatformInvitationIssued


class PublicInvitationInfo(BaseModel):
    agency_name: str
    agency_slug: str
    email: str
    name: str
    status: str
    expires_at: datetime


class PublicInvitationAccept(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    password: str = Field(min_length=1, max_length=128)


class PlatformAuditEventOut(BaseModel):
    id: uuid.UUID
    actor_name: str
    action: str
    target_agency_id: uuid.UUID | None
    resource_type: str
    resource_id: str
    details: dict
    created_at: datetime


class PlatformUsageTotal(BaseModel):
    replies: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    unpriced_replies: int


class PlatformUsageDay(BaseModel):
    date: str
    replies: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None


class PlatformUsageOut(BaseModel):
    total: PlatformUsageTotal
    days: list[PlatformUsageDay]


class PlatformInfrastructureClient(BaseModel):
    client_id: uuid.UUID
    client_name: str
    portal_slug: str
    data_mode: str
    datastore: dict | None
    storage: dict | None


class PlatformOverviewOut(BaseModel):
    agencies: int
    blocked_agencies: int
    clients: int
    agents: int
    usage: PlatformUsageTotal
    recent_events: list[PlatformAuditEventOut]
