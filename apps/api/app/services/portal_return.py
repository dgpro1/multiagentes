"""Where a channel flow lands when it comes back from a provider's hosted page.

The agency's channel screens live at ``/clients/{id}/channels/{type}``. A
client's portal admin runs the same flows from ``/portal/{slug}/channels/{type}``
(or ``/channels/{type}`` on the client's own verified domain), and the browser
must return to the screen it left, not to one the portal cannot open.

Nothing here trusts an address from the request. The landing is rebuilt from the
client's own rows (its slug, its verified domain) and the channel type, and a
``next_path`` is only ever compared with the two shapes that rebuild produces. It
is then stored server-side, in ``social_oauth_states``, with the flow it belongs
to, so the unauthenticated provider callback reads it from there and never from
its query string.
"""

import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import confined_client_id
from ..models import Client, SocialOAuthState, now_utc
from . import messaging_provider as provider

# The channel types whose flows leave the product, as the web names their screens.
SEGMENTS = frozenset({"whatsapp-cloud", "instagram", "messenger"})
# The provider tag a WhatsApp API connection is stored under; the social flows
# use their own provider names, so the two never meet.
CLOUD_FLOW = "whatsapp_cloud"
LIFETIME_MINUTES = 30


def _origin() -> tuple[str, str]:
    parsed = urlsplit((get_settings().frontend_url or "").rstrip("/"))
    return parsed.scheme, parsed.netloc


def portal_url(client: Client, segment: str, *, next_path: str | None = None) -> str:
    """The portal screen of ``segment`` for ``client``, as an absolute address.

    ``next_path`` may be left out (the app-host portal address is used) or be
    exactly one of the client's own two portal addresses for that screen;
    anything else is refused.
    """
    if segment not in SEGMENTS:
        raise HTTPException(status_code=400, detail="Unsupported messaging channel")
    scheme, netloc = _origin()
    app_path = f"/portal/{client.portal_slug}/channels/{segment}"
    root_path = f"/channels/{segment}"
    if next_path is None or next_path == app_path:
        return urlunsplit((scheme, netloc, app_path, "", ""))
    if next_path == root_path and client.portal_domain and client.portal_domain_verified:
        return f"https://{client.portal_domain}{root_path}"
    raise HTTPException(status_code=400, detail="Use this client's portal connection page as the return path")


def _latest(db: Session, client_id, flow: str) -> SocialOAuthState | None:
    return db.scalar(
        select(SocialOAuthState)
        .where(
            SocialOAuthState.provider == flow,
            SocialOAuthState.client_id == client_id,
            SocialOAuthState.used_at.is_(None),
            SocialOAuthState.expires_at > now_utc(),
        )
        .order_by(SocialOAuthState.created_at.desc())
        .limit(1)
    )


def remember(db: Session, actor, channel, flow: str = CLOUD_FLOW, segment: str = "whatsapp-cloud") -> None:
    """Note where a flow started for ``channel``'s client is to come back to.

    Whoever started the latest flow decides, so an agency person starting one
    after a portal admin retires the portal's return and lands on the panel as
    it always did. Only a portal actor leaves a return behind.
    """
    db.execute(
        update(SocialOAuthState)
        .where(SocialOAuthState.provider == flow, SocialOAuthState.client_id == channel.client_id, SocialOAuthState.used_at.is_(None))
        .values(used_at=now_utc())
    )
    if confined_client_id(actor) is not None:
        client = db.get(Client, channel.client_id)
        raw = secrets.token_urlsafe(32)
        db.add(
            SocialOAuthState(
                id=hashlib.sha256(raw.encode()).hexdigest(),
                agency_id=channel.agency_id,
                user_id=None,
                client_id=channel.client_id,
                agent_id=channel.agent_id,
                provider=flow,
                redirect_uri=provider.connect_callback_url(),
                next_url=portal_url(client, segment),
                expires_at=now_utc() + timedelta(minutes=LIFETIME_MINUTES),
            )
        )
    db.commit()


def landing(db: Session, client_id, flow: str, default: str, *, consume: bool = False) -> str:
    """The address to return to: the portal's when a portal admin started the
    flow, ``default`` (the agency's screen) otherwise."""
    state = _latest(db, client_id, flow)
    if state is None:
        return default
    if consume:
        state.used_at = now_utc()
        db.commit()
    return state.next_url
