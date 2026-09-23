"""The API credentials an agency hands to a third party.

One integration is one connection: a name, the scopes it may use and, when it
is confined to a single client, that client. Its tokens are the secrets, and
the plaintext is returned when a token is issued and never again.

Managing credentials is itself a scope (``integrations.manage``), so a token
handed to a partner cannot mint another one unless it was given that on
purpose.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api_scopes import DESCRIPTIONS, INTEGRATIONS_MANAGE, PRESETS, resolve
from ..database import get_db
from ..deps import require
from ..models import ApiIntegration, ApiToken, Client, WebhookDelivery, WebhookSubscription
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
    WebhookDeliveryOut,
    WebhookSecretOut,
    WebhookSubscriptionCreate,
    WebhookSubscriptionOut,
)
from ..security import encrypt_secret
from ..services import api_credentials
from ..services import outbound_webhooks

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


def _checked_url(url: str, *, what: str = "URL") -> str:
    """Exact-match allowlist: https everywhere, http only for loopback."""
    from urllib.parse import urlsplit

    url = (url or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {what}: {url}") from exc
    if parts.scheme == "https" and parts.hostname:
        return url
    if parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "[::1]"):
        return url
    raise HTTPException(status_code=400, detail=f"URLs must be https (http only for localhost): {url}")


def _checked_redirect_uris(uris: list[str]) -> list[str]:
    """Exact-match redirect allowlist: https everywhere, http only for
    loopback development."""
    cleaned = [_checked_url(uri, what="redirect URL") for uri in uris or [] if (uri or "").strip()]
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


def _subscription(db: Session, user, integration_id: uuid.UUID, subscription_id: uuid.UUID):
    row = _integration(db, user, integration_id)
    subscription = db.scalar(
        select(WebhookSubscription).where(
            WebhookSubscription.id == subscription_id,
            WebhookSubscription.integration_id == row.id,
        )
    )
    if subscription is None:
        raise HTTPException(status_code=404, detail="Webhook subscription not found")
    return subscription


def _subscription_out(subscription) -> dict:
    return {
        "id": subscription.id,
        "integration_id": subscription.integration_id,
        "url": subscription.url,
        "events": list(subscription.events or []),
        "is_active": subscription.is_active,
        "created_at": subscription.created_at,
        "updated_at": subscription.updated_at,
    }


@router.get("/{integration_id}/webhooks", response_model=list[WebhookSubscriptionOut])
def list_webhooks(
    integration_id: uuid.UUID,
    db: Session = Depends(get_db),
    user=Depends(require(INTEGRATIONS_MANAGE)),
):
    row = _integration(db, user, integration_id)
    return [
        _subscription_out(item)
        for item in db.scalars(
            select(WebhookSubscription)
            .where(WebhookSubscription.integration_id == row.id)
            .order_by(WebhookSubscription.created_at)
        ).all()
    ]


@router.post("/{integration_id}/webhooks", response_model=WebhookSecretOut, status_code=status.HTTP_201_CREATED)
def create_webhook(
    integration_id: uuid.UUID,
    payload: WebhookSubscriptionCreate,
    db: Session = Depends(get_db),
    user=Depends(require(INTEGRATIONS_MANAGE)),
):
    row = _integration(db, user, integration_id)
    events = sorted({event.strip() for event in payload.events or []})
    unknown = sorted(event for event in events if event not in outbound_webhooks.EVENTS)
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown event: {', '.join(unknown)}")
    secret = outbound_webhooks.new_secret()
    subscription = WebhookSubscription(
        integration_id=row.id,
        url=_checked_url(payload.url),
        encrypted_secret=encrypt_secret(secret),
        events=events,
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return {"subscription_id": subscription.id, "secret": secret}


@router.delete("/{integration_id}/webhooks/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_webhook(
    integration_id: uuid.UUID,
    subscription_id: uuid.UUID,
    db: Session = Depends(get_db),
    user=Depends(require(INTEGRATIONS_MANAGE)),
):
    db.delete(_subscription(db, user, integration_id, subscription_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{integration_id}/webhooks/{subscription_id}/deliveries", response_model=list[WebhookDeliveryOut])
def list_deliveries(
    integration_id: uuid.UUID,
    subscription_id: uuid.UUID,
    status_filter: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    user=Depends(require(INTEGRATIONS_MANAGE)),
):
    subscription = _subscription(db, user, integration_id, subscription_id)
    query = select(WebhookDelivery).where(WebhookDelivery.subscription_id == subscription.id)
    if status_filter in ("pending", "sent", "failed"):
        query = query.where(WebhookDelivery.status == status_filter)
    return list(
        db.scalars(query.order_by(WebhookDelivery.created_at.desc()).limit(limit)).all()
    )


@router.post(
    "/{integration_id}/webhooks/{subscription_id}/deliveries/{delivery_id}/replay",
    response_model=WebhookDeliveryOut,
)
def replay_delivery(
    integration_id: uuid.UUID,
    subscription_id: uuid.UUID,
    delivery_id: uuid.UUID,
    db: Session = Depends(get_db),
    user=Depends(require(INTEGRATIONS_MANAGE)),
):
    subscription = _subscription(db, user, integration_id, subscription_id)
    delivery = db.scalar(
        select(WebhookDelivery).where(
            WebhookDelivery.id == delivery_id, WebhookDelivery.subscription_id == subscription.id
        )
    )
    if delivery is None:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return outbound_webhooks.replay(db, delivery)
