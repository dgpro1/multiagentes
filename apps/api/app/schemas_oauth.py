"""Shapes of the OAuth 2.0 authorization-code flow, used only here."""

import uuid

from pydantic import BaseModel, Field


class OAuthClientInfo(BaseModel):
    client_id: str
    name: str
    scopes: list[str]
    scope_descriptions: dict[str, str]


class OAuthAuthorizeRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=40)
    redirect_uri: str = Field(min_length=1, max_length=500)
    scope: str = Field(default="", max_length=2000)
    state: str = Field(default="", max_length=500)
    approved: bool


class OAuthAuthorizeResponse(BaseModel):
    redirect_to: str


class OAuthTokenRequest(BaseModel):
    grant_type: str = Field(max_length=30)
    code: str | None = Field(default=None, max_length=200)
    redirect_uri: str | None = Field(default=None, max_length=500)
    refresh_token: str | None = Field(default=None, max_length=200)
    client_id: str = Field(min_length=1, max_length=40)
    client_secret: str | None = Field(default=None, max_length=200)


class OAuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    refresh_token: str
    scope: str


class OAuthRevokeRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    client_id: str = Field(min_length=1, max_length=40)
    client_secret: str | None = Field(default=None, max_length=200)


class ApiOAuthClientCreate(BaseModel):
    redirect_uris: list[str] = Field(default_factory=list, max_length=10)


class ApiOAuthClientOut(BaseModel):
    integration_id: uuid.UUID
    oauth_client_id: str
    redirect_uris: list[str]
    # Present only when a fresh secret was issued by this call.
    client_secret: str | None = None
