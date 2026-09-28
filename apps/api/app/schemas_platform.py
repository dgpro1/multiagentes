"""Schemas of the platform surface (SUPERADMIN -> AGENCIES -> CLIENTS)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictBool, StrictInt


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
    # Only the types the platform actually capped are listed; an absent key means
    # unlimited. "channel_used" is what each type has connected right now, so the
    # Plan tab can say "5 en uso de 5" without another request.
    channel_quotas: dict[str, int]
    channel_used: dict[str, int]


class PlatformAccessUpdate(BaseModel):
    status: Literal["active", "blocked"]
    reason: str | None = Field(default=None, max_length=500)


class PlatformFeatureEntry(BaseModel):
    key: str
    default: bool


class PlatformQuotaEntry(BaseModel):
    """A channel type the platform can put a number on."""
    key: str
    label: str
    max: int


class PlatformFeaturesOut(BaseModel):
    catalog: list[PlatformFeatureEntry]
    presets: dict[str, list[str]]
    quotas: list[PlatformQuotaEntry]


class PlatformFeaturesUpdate(BaseModel):
    features: dict[str, StrictBool]
    plan: str | None = Field(default=None, max_length=40)
    # How many lines of each channel type the agency may connect; null (or an
    # absent key) lifts the cap. Omitting the whole object leaves the numbers
    # untouched, so the Plan tab can save switches without rewriting quotas.
    channel_quotas: dict[str, StrictInt | None] | None = None


class PlatformClientOut(BaseModel):
    id: uuid.UUID
    name: str
    portal_slug: str
    is_active: bool
    data_mode: str
    agent_count: int
    created_at: datetime
    # How many lines of each channel type the agency gave this client; an absent
    # key means it draws on the agency's pool without a cap of its own.
    allocations: dict[str, int]


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


class PlatformChannelUseByClient(BaseModel):
    client_id: uuid.UUID
    client_name: str
    used: int
    allocation: int | None


class PlatformChannelUse(BaseModel):
    """One channel type: what the plan allows, what is connected, and how the
    agency spread it over its clients."""
    key: str
    label: str
    used: int
    quota: int | None
    by_client: list[PlatformChannelUseByClient]


class PlatformUsageOut(BaseModel):
    total: PlatformUsageTotal
    days: list[PlatformUsageDay]
    channels: list[PlatformChannelUse]


class PlatformInfrastructureClient(BaseModel):
    client_id: uuid.UUID
    client_name: str
    portal_slug: str
    data_mode: str
    datastore: dict | None
    storage: dict | None


class PlatformChannelLine(BaseModel):
    """One channel type across the whole installation: what is connected, how many
    agencies capped it, and how many are at or over their cap — the platform's list
    of who to talk to about a bigger plan."""
    key: str
    label: str
    used: int
    agencies_capped: int
    agencies_at_limit: int
    agencies_over_limit: int


class PlatformOverviewOut(BaseModel):
    agencies: int
    blocked_agencies: int
    clients: int
    agents: int
    usage: PlatformUsageTotal
    channels: list[PlatformChannelLine]
    recent_events: list[PlatformAuditEventOut]
