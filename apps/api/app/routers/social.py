"""Authenticated channel administration and provider callbacks."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import SocialChannel, User, now_utc
from ..schemas_social import SocialChannelOut, SocialChannelRename, SocialChannelUpdate, SocialOAuthComplete, SocialOAuthStart
from ..services import social_connections as connections
from ..services.social_graph import PROVIDERS, provider_name

router = APIRouter(prefix="/social", tags=["Messaging channels"])


@router.get("/config")
def configuration(user: User = Depends(get_current_user)):
    result = {}
    for provider in sorted(PROVIDERS):
        config = connections.get_app_config(provider)
        result[provider] = {"oauth_ready": config.ready, "manual_available": not config.managed,
                            "source": config.source, "webhook_url": config.webhook_url}
    return result


@router.get("/{provider}/clients/{client_id}/channels", response_model=list[SocialChannelOut])
def list_channels(provider: str, client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Every account of the provider connected to the client, oldest first."""
    return [connections.public_channel(item) for item in connections.client_channels(db, user, client_id, provider)]


@router.get("/{provider}/channels/{ref}", response_model=SocialChannelOut)
def get_channel(provider: str, ref: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return connections.public_channel(connections.owned_channel(db, user, ref, provider))


@router.patch("/{provider}/channels/{channel_id}", response_model=SocialChannelOut)
def rename_channel(provider: str, channel_id: uuid.UUID, payload: SocialChannelRename,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Change the agent or the name of an account without touching its authorization."""
    channel = db.scalar(select(SocialChannel).where(SocialChannel.id == channel_id, SocialChannel.agency_id == user.agency_id,
        SocialChannel.provider == provider_name(provider)))
    if not channel:
        raise HTTPException(404, "This messaging channel has not been configured")
    if payload.agent_id is not None:
        connections.owned_client(db, user, channel.client_id, payload.agent_id)
        channel.agent_id = payload.agent_id
    if "label" in payload.model_fields_set:
        channel.label = (payload.label or "").strip()[:80] or None
    channel.updated_at = now_utc()
    db.commit()
    db.refresh(channel)
    return connections.public_channel(channel)


@router.put("/{provider}/channels/{ref}", response_model=SocialChannelOut)
async def configure_channel(provider: str, ref: uuid.UUID, payload: SocialChannelUpdate,
                            db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Manual credentials are retired: every account arrives through the
    provider's hosted authorization page (see oauth/start)."""
    provider_name(provider)
    raise HTTPException(403, "Use the account authorization flow to connect this channel")


@router.post("/{provider}/channels/{ref}/connect", response_model=SocialChannelOut)
async def connect_channel(provider: str, ref: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Verify a linked account, or return the hosted page that links one."""
    from ..services import messaging_provider as provider_client
    from ..services import messaging_profiles as profiles

    channel = connections.owned_channel(db, user, ref, provider)
    provider_client.require_config()
    if channel.external_account_id:
        try:
            remote = await provider_client.require_account(channel.external_account_id, channel.provider_profile_id)
        except HTTPException as exc:
            channel.status = "error"
            channel.last_error = str(exc.detail)[:400]
            channel.updated_at = now_utc()
            db.commit()
            db.refresh(channel)
            return connections.public_channel(channel)
        channel.display_name = str(remote.get("display_name") or remote.get("username") or channel.display_name or "")[:180]
        channel.username = str(remote.get("username") or channel.username or "")[:180] or None
        channel.status = "connected"
        channel.is_enabled = True
        channel.last_error = None
        channel.last_connected_at = channel.last_connected_at or now_utc()
        channel.updated_at = now_utc()
        db.commit()
        db.refresh(channel)
        return connections.public_channel(channel)
    client = connections.owned_client(db, user, channel.client_id)
    profile_id = await profiles.ensure_client_profile(db, client)
    channel.provider_profile_id = profile_id
    db.commit()
    platform = {"instagram": "instagram", "messenger": "facebook"}[provider]
    link = await provider_client.connect_url(platform, profile_id)
    db.refresh(channel)
    return connections.public_channel(channel, connect_url=link["authorization_url"])


@router.post("/{provider}/channels/{ref}/disconnect", status_code=204)
async def disconnect_channel(provider: str, ref: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    await connections.disconnect_account(db, connections.owned_channel(db, user, ref, provider))


@router.post("/{provider}/oauth/start")
async def start_oauth(provider: str, payload: SocialOAuthStart, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    provider_name(provider)
    return {"authorization_url": await connections.begin_oauth(db, user, provider, payload.client_id, payload.agent_id, payload.next_path)}


@router.get("/{provider}/oauth/pending")
async def pending_oauth(provider: str, client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    provider_name(provider)
    return await connections.pending_oauth(db, user, provider, client_id)


@router.post("/{provider}/oauth/complete", response_model=SocialChannelOut)
async def complete_oauth(provider: str, payload: SocialOAuthComplete, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    provider_name(provider)
    return connections.public_channel(await connections.complete_oauth(db, user, provider, payload.setup_id, payload.external_account_id))


@router.post("/{provider}/channels/{ref}/import-history", status_code=202)
def import_history(provider: str, ref: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from ..services.social_history import public_job, request_import
    return public_job(request_import(db, user, ref, provider))


@router.get("/{provider}/channels/{ref}/import-history")
def history_import_status(provider: str, ref: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from ..services.social_history import latest_job, public_job
    channel = connections.owned_channel(db, user, ref, provider)
    job = latest_job(db, channel)
    if not job:
        raise HTTPException(404, "No history import has been requested for this channel")
    return public_job(job)
