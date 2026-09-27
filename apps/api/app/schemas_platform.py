"""Schemas of the platform surface (SUPERADMIN -> AGENCIES -> CLIENTS)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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


class PlatformAccessUpdate(BaseModel):
    status: Literal["active", "blocked"]
    reason: str | None = Field(default=None, max_length=500)


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
