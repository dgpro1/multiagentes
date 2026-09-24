"""The API credentials an agency hands to a third party.

One integration is one connection: a name, the scopes it may use and, when it
is confined to a single client, that client. Its tokens are the secrets, and
the plaintext is returned when a token is issued and never again.

Managing credentials is itself a scope (``integrations.manage``), so a token
handed to a partner cannot mint another one unless it was given that on
purpose.
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..api_scopes import DESCRIPTIONS, INTEGRATIONS_MANAGE, PRESETS, resolve
from ..database import get_db
from ..deps import PortalActor, confine, confined_client_id, get_current_user, require
from ..models import ApiIntegration, ApiToken, Client, User, WebhookDelivery, WebhookSubscription
from ..portal_api_access import (
    MAX_INTEGRATIONS,
    MAX_TOKENS,
    MAX_WEBHOOKS,
    allowed_scopes,
    catalogue,
    resolve_for_portal,
)
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
from ..services.tools.http_exec import _blocked_reason

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["API integrations"])


def _integration(db: Session, user, integration_id: uuid.UUID) -> ApiIntegration:
    # A portal admin sees only its own client's integrations: an agency-wide one
    # (no client) and another client's answer 404 like a row that does not exist.
    row = db.scalar(
        confine(
            select(ApiIntegration).where(
                ApiIntegration.id == integration_id,
                ApiIntegration.agency_id == user.agency_id,
            ),
            user,
            ApiIntegration.client_id,
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")
    return row


def _client_or_404(db: Session, user, client_id: uuid.UUID) -> Client:
    client = db.scalar(confine(select(Client).where(Client.id == client_id, Client.agency_id == user.agency_id), user, Client.id))
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


def _creator(db: Session, user) -> dict:
    """The columns that record who made an integration.

    ``created_by`` is the agency user its tokens act as. A portal admin is not a
    row of ``users``, so the integration is filed under the agency's owner (the
    first user) and the portal person is recorded beside it.
    """
    if not isinstance(user, PortalActor):
        return {"created_by": user.id}
    owner = db.scalar(select(User.id).where(User.agency_id == user.agency_id).order_by(User.created_at, User.id))
    if owner is None:
        raise HTTPException(status_code=409, detail="This agency has no user to file the integration under")
    return {
        "created_by": owner,
        "created_via_portal": True,
        "created_by_portal_user_id": user.id,
        "created_by_portal_label": f"{user.name} <{user.email}>"[:500],
    }


def _audit(user, action: str, row: ApiIntegration, **detail) -> None:
    """One log line for what a portal person did with credentials. Never a secret."""
    if isinstance(user, PortalActor):
        logger.info(
            "portal api: %s %s integration=%s client=%s portal_user=%s %s",
            user.email, action, row.id, row.client_id, user.id, " ".join(f"{k}={v}" for k, v in detail.items()),
        )

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


def _resolved(user, preset: str = "", scopes: list[str] | None = None) -> list[str]:
    try:
        if confined_client_id(user) is not None:
            return resolve_for_portal(user.features, preset, scopes)
        return resolve(preset, scopes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/scopes", response_model=ApiScopesOut, dependencies=[Depends(require(INTEGRATIONS_MANAGE))])
def list_scopes(user=Depends(get_current_user)):
    """The catalogue, so the interface can render the checkboxes without
    repeating the list."""
    if confined_client_id(user) is not None:
        # A portal admin is offered only what its portal may hand out.
        allowed, presets = catalogue(user.features)
        return {"scopes": [{"key": key, "description": DESCRIPTIONS[key]} for key in DESCRIPTIONS if key in allowed], "presets": presets}
    return {
        "scopes": [{"key": key, "description": DESCRIPTIONS[key]} for key in DESCRIPTIONS],
        "presets": {name: sorted(keys) for name, keys in PRESETS.items()},
    }


@router.get("", response_model=list[ApiIntegrationOut], dependencies=[Depends(require(INTEGRATIONS_MANAGE))])
def list_integrations(db: Session = Depends(get_db), user=Depends(get_current_user)):
    rows = db.scalars(
        confine(select(ApiIntegration).where(ApiIntegration.agency_id == user.agency_id), user, ApiIntegration.client_id)
        .order_by(ApiIntegration.created_at)
    ).all()
    return [_out(db, row) for row in rows]


@router.post("", response_model=ApiIntegrationOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require(INTEGRATIONS_MANAGE))])
def create_integration(
    payload: ApiIntegrationCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    scopes = _resolved(user, payload.preset, payload.scopes)
    client_id = confined_client_id(user) or payload.client_id
    if payload.client_id is not None:
        # Confined to the actor's own client: naming another one is a 404.
        _client_or_404(db, user, payload.client_id)
    if confined_client_id(user) is not None:
        held = db.scalar(select(func.count()).select_from(ApiIntegration).where(ApiIntegration.client_id == client_id))
        if held >= MAX_INTEGRATIONS:
            raise HTTPException(status_code=409, detail=f"A portal can hold at most {MAX_INTEGRATIONS} API integrations")
    row = ApiIntegration(
        agency_id=user.agency_id,
        client_id=client_id,
        name=payload.name.strip(),
        scopes=scopes,
        **_creator(db, user),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _audit(user, "created", row, scopes=",".join(scopes))
    return _out(db, row)


@router.patch("/{integration_id}", response_model=ApiIntegrationOut, dependencies=[Depends(require(INTEGRATIONS_MANAGE))])
def update_integration(
    integration_id: uuid.UUID,
    payload: ApiIntegrationUpdate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    row = _integration(db, user, integration_id)
    if payload.name is not None:
        row.name = payload.name.strip()
    if payload.scopes is not None or payload.preset is not None:
        row.scopes = _resolved(user, payload.preset or "", payload.scopes)
    db.commit()
    db.refresh(row)
    _audit(user, "updated", row, scopes=",".join(row.scopes or []))
    return _out(db, row)


@router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require(INTEGRATIONS_MANAGE))])
def delete_integration(
    integration_id: uuid.UUID,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    row = _integration(db, user, integration_id)
    _audit(user, "deleted", row)
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{integration_id}/tokens", response_model=list[ApiTokenOut], dependencies=[Depends(require(INTEGRATIONS_MANAGE))])
def list_tokens(
    integration_id: uuid.UUID,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    return list(_integration(db, user, integration_id).tokens)


@router.post("/{integration_id}/tokens", response_model=ApiTokenIssued, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require(INTEGRATIONS_MANAGE))])
def issue_token(
    integration_id: uuid.UUID,
    payload: ApiTokenCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    row = _integration(db, user, integration_id)
    if row.revoked_at is not None:
        raise HTTPException(status_code=400, detail="This integration was revoked")
    if confined_client_id(user) is not None:
        if not set(row.scopes or []) <= allowed_scopes(user.features):
            # Made or widened by the agency: only the agency hands out its credentials.
            raise HTTPException(status_code=403, detail="This integration holds access only the agency can issue tokens for")
        active = db.scalar(
            select(func.count()).select_from(ApiToken).where(
                ApiToken.integration_id == row.id, ApiToken.kind == api_credentials.LONG_LIVED, ApiToken.revoked_at.is_(None),
                or_(ApiToken.expires_at.is_(None), ApiToken.expires_at > datetime.now(timezone.utc)),
            )
        )
        if active >= MAX_TOKENS:
            raise HTTPException(status_code=409, detail=f"An integration can hold at most {MAX_TOKENS} active tokens")
    token, raw = api_credentials.issue_long_lived(db, row, days=payload.expires_in_days)
    _audit(user, "issued token", row, token=token.token_prefix)
    return {"token": raw, "token_prefix": token.token_prefix, "expires_at": token.expires_at}


@router.delete("/{integration_id}/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require(INTEGRATIONS_MANAGE))])
def revoke_token(
    integration_id: uuid.UUID,
    token_id: uuid.UUID,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    row = _integration(db, user, integration_id)
    token = db.scalar(select(ApiToken).where(ApiToken.id == token_id, ApiToken.integration_id == row.id))
    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")
    _audit(user, "revoked token", row, token=token.token_prefix)
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


def _checked_portal_url(url: str) -> str:
    """A portal admin's destination: https only, and never an address inside the
    agency's own network (the same guard the tools' URLs go through). The sender
    checks again at delivery, since a name can be re-pointed after this."""
    if not url.startswith("https://"):
        raise HTTPException(status_code=422, detail="Webhook URLs must use https")
    reason = _blocked_reason(url)
    if reason:
        raise HTTPException(status_code=422, detail=reason)
    return url


def _checked_redirect_uris(uris: list[str]) -> list[str]:
    """Exact-match redirect allowlist: https everywhere, http only for
    loopback development."""
    cleaned = [_checked_url(uri, what="redirect URL") for uri in uris or [] if (uri or "").strip()]
    if len(cleaned) > 10:
        raise HTTPException(status_code=400, detail="Register at most 10 redirect URLs")
    return cleaned


@router.post("/{integration_id}/oauth-client", response_model=ApiOAuthClientOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require(INTEGRATIONS_MANAGE))])
def setup_oauth_client(
    integration_id: uuid.UUID,
    payload: ApiOAuthClientCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
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


@router.get("/{integration_id}/webhooks", response_model=list[WebhookSubscriptionOut], dependencies=[Depends(require(INTEGRATIONS_MANAGE))])
def list_webhooks(
    integration_id: uuid.UUID,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
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


@router.post("/{integration_id}/webhooks", response_model=WebhookSecretOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require(INTEGRATIONS_MANAGE))])
def create_webhook(
    integration_id: uuid.UUID,
    payload: WebhookSubscriptionCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    row = _integration(db, user, integration_id)
    events = sorted({event.strip() for event in payload.events or []})
    unknown = sorted(event for event in events if event not in outbound_webhooks.EVENTS)
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown event: {', '.join(unknown)}")
    url = _checked_url(payload.url)
    if confined_client_id(user) is not None:
        url = _checked_portal_url(url)
        held = db.scalar(select(func.count()).select_from(WebhookSubscription).where(WebhookSubscription.integration_id == row.id))
        if held >= MAX_WEBHOOKS:
            raise HTTPException(status_code=409, detail=f"An integration can hold at most {MAX_WEBHOOKS} webhook subscriptions")
    secret = outbound_webhooks.new_secret()
    subscription = WebhookSubscription(
        integration_id=row.id,
        url=url,
        encrypted_secret=encrypt_secret(secret),
        events=events,
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    _audit(user, "created webhook", row, subscription=subscription.id)
    return {"subscription_id": subscription.id, "secret": secret}


@router.delete("/{integration_id}/webhooks/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require(INTEGRATIONS_MANAGE))])
def delete_webhook(
    integration_id: uuid.UUID,
    subscription_id: uuid.UUID,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    db.delete(_subscription(db, user, integration_id, subscription_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{integration_id}/webhooks/{subscription_id}/deliveries", response_model=list[WebhookDeliveryOut], dependencies=[Depends(require(INTEGRATIONS_MANAGE))])
def list_deliveries(
    integration_id: uuid.UUID,
    subscription_id: uuid.UUID,
    status_filter: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
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
    dependencies=[Depends(require(INTEGRATIONS_MANAGE))],
)
def replay_delivery(
    integration_id: uuid.UUID,
    subscription_id: uuid.UUID,
    delivery_id: uuid.UUID,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
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
