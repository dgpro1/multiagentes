"""The resource library and the client's storage connection: what the agency's
client page and the client portal send and read."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _clean(value: str) -> str:
    return (value or "").strip()


class StorageConnect(BaseModel):
    """The R2 credentials a customer creates in their own Cloudflare account."""

    account_id: str = Field(min_length=32, max_length=32)
    access_key_id: str = Field(min_length=8, max_length=128)
    secret_access_key: str = Field(min_length=8, max_length=256)
    bucket: str = Field(min_length=3, max_length=63)

    @field_validator("account_id", "access_key_id", "secret_access_key", "bucket")
    @classmethod
    def _strip(cls, value: str) -> str:
        return _clean(value)


class CloudflareConnect(BaseModel):
    """One Cloudflare API token from which the bucket and its keys are created."""

    token: str = Field(min_length=20, max_length=400)
    account_id: str = Field(default="", max_length=32)
    bucket: str = Field(default="", max_length=63)

    @field_validator("token", "account_id", "bucket")
    @classmethod
    def _strip(cls, value: str) -> str:
        return _clean(value)


class StorageLimitsUpdate(BaseModel):
    max_file_mb: int | None = Field(default=None, ge=1, le=20)
    quota_mb: int | None = Field(default=None, ge=1, le=10240)


class StorageConnectionOut(BaseModel):
    status: Literal["none", "pending", "connected", "error"]
    # Whose bucket holds the files, and whether the agency's is ready to take them.
    hosted_by: Literal["client", "agency"] = "client"
    agency_storage_ready: bool = False
    provider: str = "r2"
    account_id: str = ""
    bucket: str = ""
    access_key_hint: str = ""
    last_error: str | None = None
    last_checked_at: datetime | None = None
    connected_at: datetime | None = None
    max_file_mb: int
    quota_mb: int
    used_bytes: int = 0
    link_active: bool = False


class StorageLinkOut(BaseModel):
    connect_url: str
    connect_expires_at: datetime


class StorageConnectInfoOut(BaseModel):
    """What the public onboarding link shows: who it is for and its state, never credentials."""

    client_name: str
    agency_name: str
    status: Literal["pending", "connected", "error"]
    bucket: str = ""
    expires_at: datetime


class ResourceCreate(BaseModel):
    """A link resource, or the metadata of an upload that arrives as multipart."""

    kind: Literal["file", "link"] = "link"
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    is_active: bool = True
    position: int = Field(default=0, ge=0)
    url: str | None = Field(default=None, max_length=2048)
    message_template: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return check_name(value)

    @field_validator("url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return check_link(value) if value else None


def check_name(value: str) -> str:
    value = _clean(value)
    if not value:
        raise ValueError("A resource needs a name")
    # The name sits inside [Recurso: ...] in a prompt.
    if any(ch in value for ch in "[]\n\r"):
        raise ValueError("A resource name cannot contain brackets or line breaks")
    return value


class ResourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    is_active: bool | None = None
    position: int | None = Field(default=None, ge=0)
    url: str | None = Field(default=None, max_length=2048)
    message_template: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return check_name(value) if value is not None else None

    @field_validator("url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return check_link(value) if value else None


def check_link(value: str) -> str:
    value = _clean(value)
    lowered = value.lower()
    if not (lowered.startswith("https://") or lowered.startswith("http://")) or " " in value:
        raise ValueError("A link must be a full http(s) address")
    return value


class ResourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    name: str
    description: str
    is_active: bool
    position: int
    media_kind: str | None = None
    mime: str | None = None
    filename: str | None = None
    size_bytes: int = 0
    url: str | None = None
    message_template: str = ""
    created_at: datetime
    updated_at: datetime
