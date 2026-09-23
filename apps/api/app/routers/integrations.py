"""The API credentials an agency hands to a third party.

One integration is one connection: a name, the scopes it may use and, when it
is confined to a single client, that client. Its tokens are the secrets, and
the plaintext is returned when a token is issued and never again.

Managing credentials is itself a scope (``integrations.manage``), so a token
handed to a partner cannot mint another one unless it was given that on
purpose.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api_scopes import DESCRIPTIONS, INTEGRATIONS_MANAGE, PRESETS, resolve
from ..database import get_db
from ..deps import require
from ..models import ApiIntegration, ApiToken, Client
from ..schemas import (
    ApiIntegrationCreate,
    ApiIntegrationOut,
    ApiIntegrationUpdate,
    ApiOAuthClientCreate,
    ApiOAuthClientOut,
    ApiScopesOut,
    ApiTokenCreate,
    ApiTokenIssued,
    ApiTokenOut,
)
from ..services import api_credentials

router = APIRouter(prefix="/integrations", tags=["API integrations"])


def _integration(db: Session, user, integration_id: uuid.UUID) -> ApiIntegration:
    row = db.scalar(
        select(ApiIntegration).where(
            ApiIntegration.id == integration_id,
            ApiIntegration.agency_id == user.agency_id,
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")
    return row


def _client_or_404(db: Session, user, client_id: uuid.UUID) -> Client:
    client = db.scalar(select(Client).where(Client.id == client_id, Client.agency_id == user.agency_id))
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


def _out(db: Session, row: ApiIntegration) -> dict:
    client = db.get(Client, row.client_id) if row.client_id else None
    return {
        "id": row.id,
        "name": row.name,
        "client_id": row.client_id,
        "client_name": client.name if client else None,
        "scopes": list(row.scopes or []),
        "oauth_client_id": row.oauth_client_id,
        "redirect_uris": list(row.oauth_redirect_uris or []),
        "last_used_at": row.last_used_at,
        "created_at": row.created_at,
        "tokens": list(row.tokens),
    }


def _resolved(preset: str = "", scopes: list[str] | None = None) -> list[str]:
    try:
        return resolve(preset, scopes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/scopes", response_model=ApiScopesOut)
def list_scopes(user=Depends(require(INTEGRATIONS_MANAGE))):
    """The catalogue, so the interface can render the checkboxes without
    repeating the list."""
    return {
        "scopes": [{"key": key, "description": DESCRIPTIONS[key]} for key in DESCRIPTIONS],
        "presets": {name: sorted(keys) for name, keys in PRESETS.items()},
    }


@router.get("", response_model=list[ApiIntegrationOut])
def list_integrations(db: Session = Depends(get_db), user=Depends(require(INTEGRATIONS_MANAGE))):
    rows = db.scalars(
        select(ApiIntegration)
        .where(ApiIntegration.agency_id == user.agency_id)
        .order_by(ApiIntegration.created_at)
    ).all()
    return [_out(db, row) for row in rows]


@router.post("", response_model=ApiIntegrationOut, status_code=status.HTTP_201_CREATED)
def create_integration(
    payload: ApiIntegrationCreate,
    db: Session = Depends(get_db),
    user=Depends(require(INTEGRATIONS_MANAGE)),
):
    scopes = _resolved(payload.preset, payload.scopes)
    if payload.client_id is not None:
        _client_or_404(db, user, payload.client_id)
    row = ApiIntegration(
        agency_id=user.agency_id,
        client_id=payload.client_id,
        name=payload.name.strip(),
        scopes=scopes,
        created_by=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _out(db, row)


@router.patch("/{integration_id}", response_model=ApiIntegrationOut)
def update_integration(
    integration_id: uuid.UUID,
    payload: ApiIntegrationUpdate,
    db: Session = Depends(get_db),
    user=Depends(require(INTEGRATIONS_MANAGE)),
):
    row = _integration(db, user, integration_id)
    if payload.name is not None:
        row.name = payload.name.strip()
    if payload.scopes is not None or payload.preset is not None:
        row.scopes = _resolved(payload.preset or "", payload.scopes)
    db.commit()
    db.refresh(row)
    return _out(db, row)


@router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_integration(
    integration_id: uuid.UUID,
    db: Session = Depends(get_db),
    user=Depends(require(INTEGRATIONS_MANAGE)),
):
    db.delete(_integration(db, user, integration_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{integration_id}/tokens", response_model=list[ApiTokenOut])
def list_tokens(
    integration_id: uuid.UUID,
    db: Session = Depends(get_db),
    user=Depends(require(INTEGRATIONS_MANAGE)),
):
    return list(_integration(db, user, integration_id).tokens)


@router.post("/{integration_id}/tokens", response_model=ApiTokenIssued, status_code=status.HTTP_201_CREATED)
def issue_token(
    integration_id: uuid.UUID,
    payload: ApiTokenCreate,
    db: Session = Depends(get_db),
    user=Depends(require(INTEGRATIONS_MANAGE)),
):
    row = _integration(db, user, integration_id)
    if row.revoked_at is not None:
        raise HTTPException(status_code=400, detail="This integration was revoked")
    token, raw = api_credentials.issue_long_lived(db, row, days=payload.expires_in_days)
    return {"token": raw, "token_prefix": token.token_prefix, "expires_at": token.expires_at}


@router.delete("/{integration_id}/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(
    integration_id: uuid.UUID,
    token_id: uuid.UUID,
    db: Session = Depends(get_db),
    user=Depends(require(INTEGRATIONS_MANAGE)),
):
    row = _integration(db, user, integration_id)
    token = db.scalar(select(ApiToken).where(ApiToken.id == token_id, ApiToken.integration_id == row.id))
    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")
    db.delete(token)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _checked_redirect_uris(uris: list[str]) -> list[str]:
    """Exact-match redirect allowlist: https everywhere, http only for
    loopback development."""
    from urllib.parse import urlsplit

    cleaned = []
    for uri in uris or []:
        uri = (uri or "").strip()
        if not uri:
            continue
        try:
            parts = urlsplit(uri)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid redirect URL: {uri}") from exc
        if parts.scheme == "https" and parts.hostname:
            cleaned.append(uri)
        elif parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "[::1]"):
            cleaned.append(uri)
        else:
            raise HTTPException(status_code=400, detail=f"Redirect URLs must be https (http only for localhost): {uri}")
    if len(cleaned) > 10:
        raise HTTPException(status_code=400, detail="Register at most 10 redirect URLs")
    return cleaned


@router.post("/{integration_id}/oauth-client", response_model=ApiOAuthClientOut, status_code=status.HTTP_201_CREATED)
def setup_oauth_client(
    integration_id: uuid.UUID,
    payload: ApiOAuthClientCreate,
    db: Session = Depends(get_db),
    user=Depends(require(INTEGRATIONS_MANAGE)),
):
    """Turn the integration into an OAuth client: assign its public id on
    first call, rotate its secret and replace its redirect list on every
    call. The secret is returned only here, never again."""
    row = _integration(db, user, integration_id)
    if row.oauth_client_id is None:
        row.oauth_client_id = api_credentials.new_oauth_client_id()
    row.oauth_redirect_uris = _checked_redirect_uris(payload.redirect_uris)
    secret = api_credentials.new_client_secret()
    row.oauth_client_secret_hash = api_credentials.digest(secret)
    db.commit()
    db.refresh(row)
    return {
        "integration_id": row.id,
        "oauth_client_id": row.oauth_client_id,
        "redirect_uris": list(row.oauth_redirect_uris or []),
        "client_secret": secret,
    }
