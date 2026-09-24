"""Connection lifecycle through the unified messaging provider."""

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import confine, confined_client_id
from ..models import Agent, Client, SocialChannel, SocialOAuthState, User, new_public_id, now_utc
from ..security import decrypt_secret, encrypt_secret
from . import messaging_provider as provider
from . import messaging_profiles as profiles
from . import portal_return
from . import social_graph as graph


@dataclass(frozen=True)
class SocialAppConfig:
    provider: str
    app_id: str = ""
    app_secret: str = ""
    redirect_uri: str = ""
    webhook_url: str = ""
    verify_token: str = ""
    source: str = "provider"
    login_config_id: str = ""
    frontend_url: str = ""
    human_agent_enabled: bool = False

    @property
    def callback_url(self):
        return self.redirect_uri

    @property
    def managed(self):
        # Credentials are never entered by hand: every account arrives
        # through the provider's hosted authorization page.
        return True

    @property
    def ready(self):
        return provider.configured()


_app_resolver: Callable | None = None
_connection_hooks: list[Callable] = []
_state_hooks: list[Callable] = []


def register_app_config_resolver(resolver: Callable | None) -> None:
    global _app_resolver
    _app_resolver = resolver


def register_connection_hook(hook: Callable) -> None:
    if hook not in _connection_hooks:
        _connection_hooks.append(hook)


def register_oauth_state_hook(hook: Callable) -> None:
    if hook not in _state_hooks:
        _state_hooks.append(hook)


def _https_origin(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def get_app_config(provider_name: str) -> SocialAppConfig:
    graph.provider_name(provider_name)
    if _app_resolver:
        return _app_resolver(provider_name)
    settings = get_settings()
    origin = provider.public_base()
    human_agent = bool(getattr(settings, f"{provider_name}_human_agent_enabled", False))
    return SocialAppConfig(
        provider=provider_name,
        redirect_uri=provider.connect_callback_url(),
        webhook_url=provider.webhook_url(),
        frontend_url=settings.frontend_url,
        human_agent_enabled=human_agent,
    )


def owned_client(db: Session, user: User, client_id, agent_id=None):
    # A client's portal admin reaches only its own client; see PortalActor.
    client = db.scalar(confine(select(Client).where(Client.id == client_id, Client.agency_id == user.agency_id), user, Client.id))
    if not client:
        raise HTTPException(404, "Client not found")
    if agent_id is not None and not db.scalar(select(Agent.id).where(Agent.id == agent_id, Agent.client_id == client.id, Agent.agency_id == user.agency_id)):
        raise HTTPException(400, "Select an agent that belongs to this client")
    return client


def owned_channel(db: Session, user: User, ref, provider_name: str):
    """``ref`` is a channel id, or a client id for that client's first account
    of the provider (the shape the routes had while a client could only have one)."""
    graph.provider_name(provider_name)
    channel = db.scalar(confine(select(SocialChannel).where(SocialChannel.id == ref, SocialChannel.agency_id == user.agency_id,
        SocialChannel.provider == provider_name), user, SocialChannel.client_id))
    if channel:
        return channel
    owned_client(db, user, ref)
    channel = db.scalar(select(SocialChannel).where(SocialChannel.client_id == ref, SocialChannel.agency_id == user.agency_id,
        SocialChannel.provider == provider_name).order_by(SocialChannel.created_at).limit(1))
    if not channel:
        raise HTTPException(404, "This messaging channel has not been configured")
    return channel


def client_channels(db: Session, user: User, client_id, provider_name: str) -> list[SocialChannel]:
    graph.provider_name(provider_name)
    owned_client(db, user, client_id)
    return db.scalars(select(SocialChannel).where(SocialChannel.client_id == client_id, SocialChannel.agency_id == user.agency_id,
        SocialChannel.provider == provider_name).order_by(SocialChannel.created_at)).all()


def public_channel(channel: SocialChannel, connect_url: str | None = None) -> dict:
    config = get_app_config(channel.provider)
    keys = ("id", "client_id", "agent_id", "provider", "external_account_id", "app_id", "display_name", "username", "label", "status", "is_enabled", "token_expires_at", "last_error", "human_agent_enabled", "connection_source", "granted_scopes", "last_connected_at", "created_at", "updated_at")
    return {**{key: getattr(channel, key) for key in keys}, "webhook_url": config.webhook_url,
            "webhook_verify_token": None,
            "has_access_token": False, "has_app_secret": False,
            "connect_url": connect_url}


async def connect_account(db: Session, user: User, client_id, agent_id, provider_name: str, account: dict, *, source: str, human_agent_enabled=True, activate=True, channel: SocialChannel | None = None, label: str | None = None) -> SocialChannel:
    """Bind a provider account to the client after remote validation.

    The client's row for this account is updated, or a new one is added: a
    disconnected row keeps its history but releases the account, so the same
    account can be connected under another client.
    """
    client = owned_client(db, user, client_id, agent_id)
    graph.provider_name(provider_name)
    account_id = str(account.get("id") or account.get("account_id") or "").strip()
    if not account_id:
        raise HTTPException(400, "Select one of the accounts authorized by this connection")
    # A disconnected channel keeps its row for history but releases the
    # account, so the account is free to be connected under another client.
    collision = db.scalar(select(SocialChannel.id).where(SocialChannel.provider == provider_name, SocialChannel.external_account_id == account_id,
        SocialChannel.client_id != client_id, SocialChannel.status == "connected"))
    if collision:
        raise HTTPException(409, "This account is already connected to another client")
    if channel is not None:
        if channel.client_id != client_id or channel.provider != provider_name:
            raise HTTPException(404, "This messaging channel has not been configured")
        if channel.external_account_id and channel.external_account_id != account_id:
            raise HTTPException(409, "An existing channel cannot be reassigned to a different account. Connect the other account as a new one to preserve conversation routing")
    else:
        channel = db.scalar(select(SocialChannel).where(SocialChannel.client_id == client_id, SocialChannel.agency_id == user.agency_id,
            SocialChannel.provider == provider_name, SocialChannel.external_account_id == account_id).with_for_update())
    remote = await provider.require_account(account_id)
    name = str(remote.get("display_name") or remote.get("username") or account.get("name") or account_id)
    username = remote.get("username") or account.get("username")
    try:
        if not channel:
            channel = SocialChannel(agency_id=user.agency_id, client_id=client_id, agent_id=agent_id, provider=provider_name, webhook_verify_token=new_public_id())
            db.add(channel)
        channel.agent_id = agent_id
        if label is not None:
            channel.label = label.strip()[:80] or None
        channel.external_account_id = account_id
        channel.provider_profile_id = client.provider_profile_id
        channel.display_name = name[:180]
        channel.username = str(username)[:180] if username else None
        channel.encrypted_access_token = None
        channel.encrypted_app_secret = None
        channel.token_expires_at = None
        channel.granted_scopes = []
        channel.connection_source = source
        channel.status = "connected" if activate else "disconnected"
        channel.is_enabled = activate
        channel.human_agent_enabled = human_agent_enabled
        channel.last_connected_at = now_utc() if activate else channel.last_connected_at
        channel.updated_at = now_utc()
        channel.last_error = None
        db.flush()
        if activate:
            for hook in _connection_hooks:
                hook(db, channel, "linked")
        db.commit()
    except Exception as exc:
        db.rollback()
        if isinstance(exc, IntegrityError):
            raise HTTPException(409, "This account is already connected") from None
        raise
    db.refresh(channel)
    return channel


async def disconnect_account(db: Session, channel: SocialChannel) -> None:
    """Remove the channel. Its conversations stay as history; the provider
    account itself is untouched and can be connected again."""
    for hook in _connection_hooks:
        hook(db, channel, "unlinked")
    db.delete(channel)
    db.commit()


def _payload(state: SocialOAuthState) -> dict:
    try:
        return json.loads(decrypt_secret(state.encrypted_payload))
    except (ValueError, TypeError):
        raise HTTPException(400, "This connection request is invalid") from None


def _new_state(db, user, client_id, agent_id, provider_name, next_url, payload):
    from ..security import encrypt_secret

    raw = secrets.token_urlsafe(32)
    state = SocialOAuthState(id=hashlib.sha256(raw.encode()).hexdigest(), agency_id=user.agency_id,
        # A portal admin is not a row of users; the state then names nobody.
        user_id=None if confined_client_id(user) is not None else user.id, client_id=client_id, agent_id=agent_id, provider=provider_name,
        redirect_uri=provider.connect_callback_url(), next_url=next_url,
        encrypted_payload=encrypt_secret(json.dumps(payload)),
        expires_at=now_utc() + timedelta(minutes=max(1, min(get_settings().social_oauth_state_minutes, 30))))
    db.add(state)
    db.flush()
    for hook in _state_hooks:
        hook(db, state)
    db.commit()
    return raw, state


def _platform_of(provider_name: str) -> str:
    return {"instagram": "instagram", "messenger": "facebook"}[provider_name]


async def begin_oauth(db: Session, user: User, provider_name: str, client_id, agent_id, next_path: str | None) -> str:
    graph.provider_name(provider_name)
    client = owned_client(db, user, client_id, agent_id)
    provider.require_config()
    if confined_client_id(user) is not None:
        # From the portal the flow returns to the portal's own screen.
        next_url = portal_return.portal_url(client, provider_name, next_path=next_path)
    else:
        path = next_path or f"/clients/{client_id}/channels/{provider_name}"
        # Return only to the connection screen of the client bound into this state.
        if path != f"/clients/{client_id}/channels/{provider_name}":
            raise HTTPException(400, "Use this client's messaging connection page as the return path")
        origin = (get_settings().frontend_url or "").rstrip("/")
        parsed = urlsplit(origin)
        next_url = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    _new_state(db, user, client_id, agent_id, provider_name, next_url, {"phase": "link"})
    profile_id = await profiles.ensure_client_profile(db, client)
    link = await provider.connect_url(_platform_of(provider_name), profile_id)
    return link["authorization_url"]


async def pending_oauth(db: Session, user: User, provider_name: str, client_id) -> dict:
    """The accounts on the client's profile the operator can bind."""
    graph.provider_name(provider_name)
    client = owned_client(db, user, client_id)
    provider.require_config()
    if not client.provider_profile_id:
        raise HTTPException(404, "No pending connection was found. Start the connection again")
    accounts = await provider.list_accounts(client.provider_profile_id)
    return {"setup_id": client.provider_profile_id,
            "accounts": [{"id": item["account_id"], "name": item.get("display_name") or item.get("username"),
                           "username": item.get("username")} for item in accounts]}


async def complete_oauth(db: Session, user: User, provider_name: str, setup_id: str, account_id: str) -> SocialChannel:
    """Bind one of the profile's accounts to the client."""
    graph.provider_name(provider_name)
    provider.require_config()
    client = db.scalar(confine(select(Client).where(Client.provider_profile_id == setup_id, Client.agency_id == user.agency_id), user, Client.id))
    if not client:
        raise HTTPException(400, "Start the connection again with the current application")
    agent = db.scalar(select(Agent).where(Agent.client_id == client.id, Agent.agency_id == user.agency_id,
        Agent.deleted_at.is_(None)).order_by(Agent.created_at).limit(1))
    state = db.scalar(select(SocialOAuthState).where(SocialOAuthState.provider == provider_name,
        SocialOAuthState.agency_id == user.agency_id, SocialOAuthState.user_id == user.id,
        SocialOAuthState.client_id == client.id, SocialOAuthState.used_at.is_(None),
        SocialOAuthState.expires_at > now_utc()).order_by(SocialOAuthState.created_at.desc()).limit(1))
    agent_id = state.agent_id if state else (agent.id if agent else None)
    if not agent_id:
        raise HTTPException(400, "Add an agent to this client before connecting")
    owned_client(db, user, client.id, agent_id)
    if state:
        state.used_at = now_utc()
    accounts = await provider.list_accounts(setup_id)
    account = next((item for item in accounts if item["account_id"] == account_id), None)
    if not account:
        raise HTTPException(400, "Select one of the accounts authorized by this connection")
    return await connect_account(db, user, client.id, agent_id, provider_name,
        {"id": account_id, "name": account.get("display_name"), "username": account.get("username")},
        source="oauth", human_agent_enabled=True)


async def bind_callback_account(db, provider_name: str, profile_id: str, account_id: str) -> tuple[SocialChannel, str]:
    """Bind the account the operator just approved on the hosted page.
    Returns the channel and the frontend path to land on."""
    graph.provider_name(provider_name)
    client = db.scalar(select(Client).where(Client.provider_profile_id == profile_id).limit(1))
    if not client:
        raise HTTPException(400, "This connection request is invalid or expired")
    state = db.scalar(select(SocialOAuthState).where(SocialOAuthState.provider == provider_name,
        SocialOAuthState.client_id == client.id, SocialOAuthState.used_at.is_(None),
        SocialOAuthState.expires_at > now_utc()).order_by(SocialOAuthState.created_at.desc()).limit(1))
    if state:
        state.used_at = now_utc()
        user = db.get(User, state.user_id) if state.user_id else None
        agent_id, next_url = state.agent_id, state.next_url
    else:
        user = None
        agent = db.scalar(select(Agent).where(Agent.client_id == client.id,
            Agent.deleted_at.is_(None)).order_by(Agent.created_at).limit(1))
        agent_id, next_url = (agent.id if agent else None), f"/clients/{client.id}/channels/{provider_name}"
    if not user or user.agency_id != client.agency_id:
        owner = db.scalar(select(User).where(User.agency_id == client.agency_id).order_by(User.created_at).limit(1))
        if not owner or not agent_id:
            raise HTTPException(400, "Add an agent to this client before connecting")
        user = owner
    accounts = await provider.list_accounts(profile_id)
    account = next((item for item in accounts if item["account_id"] == account_id), None)
    if not account:
        raise HTTPException(400, "The approved account is not on this profile yet. Retry in a moment")
    channel = await connect_account(db, user, client.id, agent_id, provider_name,
        {"id": account_id, "name": account.get("display_name"), "username": account.get("username")},
        source="oauth", human_agent_enabled=True)
    return channel, next_url


async def refresh_due_channels(db: Session) -> None:
    """Prune expired states and confirm connected accounts still exist."""
    current = now_utc()
    db.execute(delete(SocialOAuthState).where(SocialOAuthState.expires_at <= current))
    db.commit()
    if not provider.configured():
        return
    channels = db.scalars(select(SocialChannel).where(SocialChannel.is_enabled.is_(True),
        SocialChannel.status == "connected")).all()
    for channel in channels:
        if channel.token_refresh_attempted_at and channel.token_refresh_attempted_at > current - timedelta(hours=24):
            continue
        channel.token_refresh_attempted_at = current
        db.commit()
        try:
            remote = await provider.require_account(channel.external_account_id, channel.provider_profile_id)
            channel.display_name = str(remote.get("display_name") or remote.get("username") or channel.display_name or "")[:180]
            channel.username = str(remote.get("username") or channel.username or "")[:180] or None
            channel.last_error = None
        except HTTPException as exc:
            if exc.status_code == 404:
                channel.status = "error"
            channel.last_error = str(exc.detail)[:400]
        channel.updated_at = current
        db.commit()


# The lifecycle name is also used by resource deletion handlers.
disconnect_channel = disconnect_account
