import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..api_scopes import CHANNELS_MANAGE, CHANNELS_READ
from ..deps import confine, get_current_user, require
from ..models import Agent, Client, User, WhatsAppCloudChannel, new_public_id, now_utc
from ..schemas_whatsapp_cloud import WhatsAppCloudChannelOut, WhatsAppCloudChannelUpdate
from ..services import messaging_provider as provider
from ..services import messaging_profiles as profiles
from ..services import portal_return
from ..services.whatsapp_cloud import verify_account


router = APIRouter(prefix="/whatsapp-cloud", tags=["WhatsApp Cloud"])


def _channel_for_user(db: Session, user: User, ref: uuid.UUID) -> WhatsAppCloudChannel:
    """``ref`` is a channel id, or a client id for that client's first number
    (the shape these routes had while a client could only have one)."""
    channel = db.scalar(
        confine(
            select(WhatsAppCloudChannel).where(WhatsAppCloudChannel.id == ref, WhatsAppCloudChannel.agency_id == user.agency_id),
            user, WhatsAppCloudChannel.client_id,
        )
    )
    if channel:
        return channel
    channel = db.scalar(
        confine(
            select(WhatsAppCloudChannel).where(WhatsAppCloudChannel.client_id == ref, WhatsAppCloudChannel.agency_id == user.agency_id),
            user, WhatsAppCloudChannel.client_id,
        )
        .order_by(WhatsAppCloudChannel.created_at)
        .limit(1)
    )
    if not channel:
        raise HTTPException(status_code=404, detail="This client does not have the WhatsApp API configured yet")
    return channel


def _owned_client(db: Session, user: User, client_id: uuid.UUID) -> Client:
    # A client's portal admin reaches only its own client; see PortalActor.
    client = db.scalar(confine(select(Client).where(Client.id == client_id, Client.agency_id == user.agency_id), user, Client.id))
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


def _client_agent(db: Session, user: User, client_id: uuid.UUID, agent_id: uuid.UUID) -> Agent:
    agent = db.scalar(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.client_id == client_id,
            Agent.agency_id == user.agency_id,
            Agent.deleted_at.is_(None),
        )
    )
    if not agent:
        raise HTTPException(status_code=400, detail="Select an agent that belongs to this client")
    return agent


def _public_channel(channel: WhatsAppCloudChannel, connect_url: str | None = None) -> dict:
    return {
        "id": channel.id,
        "client_id": channel.client_id,
        "agent_id": channel.agent_id,
        "status": channel.status,
        "phone_number": channel.phone_number,
        "display_name": channel.display_name,
        "label": channel.label,
        "phone_number_id": channel.phone_number_id,
        "waba_id": channel.waba_id,
        "external_account_id": channel.external_account_id,
        "provider_profile_id": channel.provider_profile_id,
        "connect_url": connect_url,
        "coexistence": channel.coexistence,
        "coexistence_sync": channel.coexistence_sync,
        "quality_rating": channel.quality_rating,
        "messaging_limit": channel.messaging_limit,
        "has_access_token": False,
        "has_app_secret": False,
        "webhook_url": provider.webhook_url() if provider.configured() else "",
        "webhook_verify_token": channel.webhook_verify_token,
        "last_error": channel.last_error,
        "is_enabled": channel.is_enabled,
        "last_connected_at": channel.last_connected_at,
        "created_at": channel.created_at,
        "updated_at": channel.updated_at,
    }


@router.get("/clients/{client_id}/channels", response_model=list[WhatsAppCloudChannelOut], dependencies=[Depends(require(CHANNELS_READ))])
def list_channels(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Every WhatsApp API number of the client, oldest first."""
    client = _owned_client(db, user, client_id)
    rows = db.scalars(
        select(WhatsAppCloudChannel).where(WhatsAppCloudChannel.client_id == client.id).order_by(WhatsAppCloudChannel.created_at)
    ).all()
    return [_public_channel(item) for item in rows]


def _apply_update(db: Session, user: User, channel: WhatsAppCloudChannel, payload: WhatsAppCloudChannelUpdate) -> None:
    agent = _client_agent(db, user, channel.client_id, payload.agent_id)
    channel.agent_id = agent.id
    if "label" in payload.model_fields_set:
        channel.label = (payload.label or "").strip()[:80] or None
    channel.is_enabled = True
    channel.updated_at = now_utc()


@router.post("/clients/{client_id}/channels", response_model=WhatsAppCloudChannelOut, status_code=201, dependencies=[Depends(require(CHANNELS_MANAGE))])
def create_channel(
    client_id: uuid.UUID,
    payload: WhatsAppCloudChannelUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Add another WhatsApp API number to the client. Linking the number
    itself happens on connect, through the provider's hosted page."""
    client = _owned_client(db, user, client_id)
    channel = WhatsAppCloudChannel(
        agency_id=user.agency_id, client_id=client.id, agent_id=payload.agent_id, webhook_verify_token=new_public_id()
    )
    _apply_update(db, user, channel, payload)
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return _public_channel(channel)


@router.get("/channels/{ref}", response_model=WhatsAppCloudChannelOut, dependencies=[Depends(require(CHANNELS_READ))])
def get_channel(ref: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _public_channel(_channel_for_user(db, user, ref))


async def _sync_from_provider(db: Session, channel: WhatsAppCloudChannel) -> None:
    """Refresh the number's public profile from its linked account."""
    if not channel.external_account_id:
        raise HTTPException(status_code=409, detail="Link a number on the connection page before refreshing")
    try:
        profile = await verify_account(channel.external_account_id, channel.provider_profile_id)
    except HTTPException as exc:
        channel.status = "error"
        channel.last_error = str(exc.detail)
        channel.updated_at = now_utc()
        db.commit()
        db.refresh(channel)
        return
    channel.status = "connected"
    channel.phone_number = profile.get("display_phone_number")
    channel.display_name = profile.get("verified_name")
    channel.quality_rating = profile.get("quality_rating")
    channel.messaging_limit = profile.get("messaging_limit")
    channel.coexistence = False
    channel.last_error = None
    channel.is_enabled = True
    channel.last_connected_at = channel.last_connected_at or now_utc()
    channel.updated_at = now_utc()
    db.commit()
    db.refresh(channel)


@router.post("/channels/{ref}/refresh", response_model=WhatsAppCloudChannelOut, dependencies=[Depends(require(CHANNELS_MANAGE))])
async def refresh_channel(ref: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    channel = _channel_for_user(db, user, ref)
    await _sync_from_provider(db, channel)
    return _public_channel(channel)


@router.put("/channels/{ref}", response_model=WhatsAppCloudChannelOut, dependencies=[Depends(require(CHANNELS_MANAGE))])
def configure_channel(
    ref: uuid.UUID,
    payload: WhatsAppCloudChannelUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Save a number's agent and name. Called with a client id it
    configures that client's first number, creating it when there is none."""
    channel = db.scalar(
        confine(
            select(WhatsAppCloudChannel).where(WhatsAppCloudChannel.id == ref, WhatsAppCloudChannel.agency_id == user.agency_id),
            user, WhatsAppCloudChannel.client_id,
        )
    )
    if not channel:
        client = _owned_client(db, user, ref)
        channel = db.scalar(
            select(WhatsAppCloudChannel).where(WhatsAppCloudChannel.client_id == client.id)
            .order_by(WhatsAppCloudChannel.created_at).limit(1)
        )
        if not channel:
            channel = WhatsAppCloudChannel(
                agency_id=user.agency_id, client_id=client.id, agent_id=payload.agent_id, webhook_verify_token=new_public_id()
            )
            db.add(channel)
    _apply_update(db, user, channel, payload)
    db.commit()
    db.refresh(channel)
    return _public_channel(channel)


@router.delete("/channels/{channel_id}", status_code=204, dependencies=[Depends(require(CHANNELS_MANAGE))])
async def remove_channel(channel_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Remove a number. Its conversations stay as history; the provider-side
    profile is released so the name can be used again (best-effort)."""
    channel = db.scalar(
        confine(
            select(WhatsAppCloudChannel).where(WhatsAppCloudChannel.id == channel_id, WhatsAppCloudChannel.agency_id == user.agency_id),
            user, WhatsAppCloudChannel.client_id,
        )
    )
    if not channel:
        raise HTTPException(status_code=404, detail="Number not found")
    from ..services.messaging_profiles import release_channel_profile

    await release_channel_profile(channel)
    db.delete(channel)
    db.commit()


@router.post("/channels/{ref}/connect", response_model=WhatsAppCloudChannelOut, dependencies=[Depends(require(CHANNELS_MANAGE))])
async def connect_channel(ref: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Link the number through the provider's hosted page. When the number
    is already linked this verifies it instead and returns the channel;
    otherwise it returns the channel with the page to open."""
    channel = _channel_for_user(db, user, ref)
    provider.require_config()
    if channel.external_account_id:
        await _sync_from_provider(db, channel)
        if channel.status == "connected":
            channel.last_connected_at = now_utc()
            channel.updated_at = now_utc()
            db.commit()
            db.refresh(channel)
        return _public_channel(channel)
    profile_id = await profiles.ensure_channel_profile(db, channel)
    # Note where the hosted page's callback sends the browser back to: the portal
    # screen when a portal admin started this, the panel's otherwise.
    portal_return.remember(db, user, channel)
    try:
        link = await provider.connect_url("whatsapp", profile_id, onboarding="api")
    except HTTPException as exc:
        channel.status = "error"
        channel.last_error = str(exc.detail)
        channel.updated_at = now_utc()
        db.commit()
        db.refresh(channel)
        return _public_channel(channel)
    db.refresh(channel)
    return _public_channel(channel, connect_url=link["authorization_url"])


@router.post("/channels/{ref}/disconnect", response_model=WhatsAppCloudChannelOut, dependencies=[Depends(require(CHANNELS_MANAGE))])
def disconnect_channel(ref: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    channel = _channel_for_user(db, user, ref)
    channel.status = "disconnected"
    channel.is_enabled = False
    channel.last_error = None
    channel.updated_at = now_utc()
    db.commit()
    db.refresh(channel)
    return _public_channel(channel)
