"""Provider-side profile bookkeeping.

The provider groups accounts into profiles: social accounts share their
client's profile, while each WhatsApp number lives on its own profile
(one number per profile). Ids are stored on the client/channel rows so a
reconnect reuses the same profile instead of scattering accounts.
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import Client
from . import messaging_provider as provider


def _profile_name_conflict(exc: HTTPException) -> bool:
    return exc.status_code == 502 and "profile_name_conflict" in str(exc.detail)


async def _create_profile_unique(name: str, unique_part: str) -> str:
    """Create the profile, and on the provider's name-conflict error (an
    orphan from a deleted line still holding accounts) retry once with a
    suffix the deleted row can never collide with."""
    try:
        return await provider.create_profile(name)
    except HTTPException as exc:
        if not _profile_name_conflict(exc) or not unique_part:
            raise
    suffix = "".join(ch for ch in unique_part if ch.isalnum())[:8]
    return await provider.create_profile(f"{name} {suffix}")


async def release_channel_profile(channel) -> None:
    """Best-effort: drop the number's provider profile when the channel row
    goes away. Zernio refuses when the profile still holds a connected
    account, and a provider that is down must not block the deletion, so
    every failure is swallowed and the id simply cleared."""
    profile_id = getattr(channel, "provider_profile_id", None)
    if not profile_id:
        return
    try:
        await provider.delete_profile(profile_id)
    except Exception:  # noqa: BLE001 - the row goes away regardless
        pass
    channel.provider_profile_id = None


async def release_client_profile(client: Client) -> None:
    """Best-effort: drop the client's shared social profile with the client."""
    profile_id = getattr(client, "provider_profile_id", None)
    if not profile_id:
        return
    try:
        await provider.delete_profile(profile_id)
    except Exception:  # noqa: BLE001 - the row goes away regardless
        pass
    client.provider_profile_id = None


async def ensure_client_profile(db: Session, client: Client) -> str:
    """The shared profile for the client's Instagram/Messenger accounts."""
    provider.require_config()
    if client.provider_profile_id:
        try:
            known = {item.get("_id") for item in await provider.list_profiles() if isinstance(item, dict)}
            if client.provider_profile_id in known:
                return client.provider_profile_id
        except HTTPException:
            pass
    name = f"client {(client.name or '').strip()[:80] or 'business'}"
    profile_id = await _create_profile_unique(name, str(client.id))
    client.provider_profile_id = profile_id
    db.commit()
    return profile_id


async def ensure_channel_profile(db: Session, channel) -> str:
    """A dedicated profile for one WhatsApp number."""
    provider.require_config()
    if getattr(channel, "provider_profile_id", None):
        try:
            known = {item.get("_id") for item in await provider.list_profiles() if isinstance(item, dict)}
            if channel.provider_profile_id in known:
                return channel.provider_profile_id
        except HTTPException:
            pass
    label = (getattr(channel, "label", None) or getattr(channel, "phone_number", None) or "line").strip()[:80]
    profile_id = await _create_profile_unique(f"line {label}", str(channel.id))
    channel.provider_profile_id = profile_id
    db.commit()
    return profile_id
