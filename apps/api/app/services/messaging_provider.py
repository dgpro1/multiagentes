"""Client for the unified upstream messaging provider.

One server key serves every messaging channel (WhatsApp numbers, Instagram
accounts, Messenger Pages). Channels keep only the provider-side account id;
threads are addressed by the provider conversation id stored on each
conversation. Errors surface as HTTPException with safe messages (the key
and raw payloads never leave this module).
"""

import hashlib
import hmac
import logging

import httpx
from fastapi import HTTPException

from ..config import get_settings

logger = logging.getLogger(__name__)

TIMEOUT = 30
MAX_MEDIA_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

PLATFORMS = {"whatsapp", "instagram", "facebook"}

# Events this backend handles. Subscribed on the shared webhook endpoint.
INBOX_EVENTS = [
    "message.received",
    "message.sent",
    "message.delivered",
    "message.read",
    "message.failed",
    "message.edited",
    "message.deleted",
    "reaction.received",
    "conversation.started",
    "conversation.control_changed",
    "account.connected",
    "account.disconnected",
    "whatsapp.template.status_updated",
]

# Header upstream deliveries are signed with (hex HMAC-SHA256 of the raw
# body keyed by the webhook secret), plus its legacy alias.
_SIGNATURE_HEADERS = ("x-zernio-signature", "x-late-signature")


def configured() -> bool:
    return bool(get_settings().messaging_provider_api_key.strip())


def require_config() -> None:
    if not configured():
        raise HTTPException(
            status_code=409,
            detail="The messaging provider is not configured. Set MESSAGING_PROVIDER_API_KEY on the server.",
        )


def public_base() -> str:
    settings = get_settings()
    return (
        settings.messaging_provider_public_url
        or settings.social_public_url
        or settings.frontend_url
    ).rstrip("/")


def webhook_url() -> str:
    return f"{public_base()}/api/public/messaging/webhook"


def connect_callback_url() -> str:
    return f"{public_base()}/api/public/messaging/connect/callback"


def _url(path: str) -> str:
    return f"{get_settings().messaging_provider_base_url.rstrip('/')}/{path.lstrip('/')}"


def _headers(*, idempotency_key: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {get_settings().messaging_provider_api_key.strip()}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _safe_error(body: dict, status: int) -> str:
    message = body.get("error") if isinstance(body.get("error"), str) else None
    code = body.get("code")
    detail = str(message or "the request was rejected").strip()[:300]
    return f"{code}: {detail}" if code else detail


async def _request(method: str, path: str, **kwargs) -> dict:
    require_config()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.request(method, _url(path), headers=_headers(
                idempotency_key=kwargs.pop("idempotency_key", None)), **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach the messaging provider.") from exc
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise HTTPException(status_code=502, detail="The messaging provider returned an oversized response.")
    try:
        body = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="The messaging provider returned an invalid response.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=502, detail="The messaging provider returned an invalid response.")
    if response.status_code == 401:
        raise HTTPException(status_code=401, detail="The messaging provider rejected the server key.")
    if response.status_code == 429:
        raise HTTPException(status_code=429, detail="The messaging provider rate limit was reached. Retry later.")
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail=_safe_error(body, 404))
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"The messaging provider rejected the request: {_safe_error(body, response.status_code)}")
    return body


async def _request_with_status(method: str, path: str, **kwargs) -> tuple[int, dict]:
    """Raw variant for callers that branch on validation codes themselves."""
    require_config()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.request(method, _url(path), headers=_headers(
                idempotency_key=kwargs.pop("idempotency_key", None)), **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach the messaging provider.") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body if isinstance(body, dict) else {}


def _account_summary(raw: dict) -> dict:
    return {
        "account_id": str(raw.get("_id") or raw.get("accountId") or ""),
        "platform": str(raw.get("platform") or ""),
        "username": raw.get("username"),
        "display_name": raw.get("displayName") or raw.get("verifiedName") or raw.get("name"),
        "phone_number": raw.get("phoneNumber") or raw.get("phone_number"),
        "is_active": raw.get("isActive", True),
        "profile_id": str(raw.get("profileId") or ""),
        "raw": raw,
    }


# Profiles (one per brand, or one per WhatsApp number: a profile holds a
# single number, while social accounts share their client's profile).

async def create_profile(name: str) -> str:
    body = await _request("POST", "/profiles", json={"name": (name or "").strip()[:120] or "Channel"})
    profile = body.get("profile") or {}
    profile_id = str(profile.get("_id") or "")
    if not profile_id:
        raise HTTPException(status_code=502, detail="The messaging provider did not return a profile.")
    return profile_id


async def list_profiles() -> list[dict]:
    body = await _request("GET", "/profiles")
    profiles = body.get("profiles")
    return profiles if isinstance(profiles, list) else []


async def delete_profile(profile_id: str) -> None:
    """Delete a provider profile. Per the Zernio API the profile must hold
    no connected accounts; a 404 means it is already gone, which is fine."""
    if not (profile_id or "").strip():
        return
    try:
        await _request("DELETE", f"/profiles/{profile_id.strip()}")
    except HTTPException as exc:
        if exc.status_code != 404:
            raise


# Connect flow (OAuth-style: the operator approves on the hosted page and
# lands back on the callback with the account bound to the profile).

async def connect_url(platform: str, profile_id: str, *, onboarding: str | None = None) -> dict:
    if platform not in PLATFORMS:
        raise HTTPException(status_code=404, detail="Unsupported messaging channel")
    params = {"profileId": profile_id, "redirect_url": connect_callback_url()}
    if onboarding:
        params["onboarding"] = onboarding
    body = await _request("GET", f"/connect/{platform}", params=params)
    auth_url = str(body.get("authUrl") or "")
    if not auth_url:
        raise HTTPException(status_code=502, detail="The messaging provider did not return a connection page.")
    return {"authorization_url": auth_url, "state": str(body.get("state") or "")}


# Accounts.

async def list_accounts(profile_id: str | None = None) -> list[dict]:
    params = {"profileId": profile_id} if profile_id else None
    body = await _request("GET", "/accounts", params=params)
    accounts = body.get("accounts")
    return [_account_summary(item) for item in accounts] if isinstance(accounts, list) else []


async def find_account(account_id: str, profile_id: str | None = None) -> dict | None:
    wanted = (account_id or "").strip()
    if not wanted:
        return None
    for account in await list_accounts(profile_id):
        if account["account_id"] == wanted:
            return account
    if profile_id:
        for account in await list_accounts(None):
            if account["account_id"] == wanted:
                return account
    return None


async def require_account(account_id: str, profile_id: str | None = None) -> dict:
    account = await find_account(account_id, profile_id)
    if not account:
        raise HTTPException(status_code=404, detail="The messaging account is not connected on the provider")
    return account


async def account_health(account_id: str) -> dict:
    body = await _request("GET", f"/accounts/{account_id}/health")
    return body if isinstance(body, dict) else {}


# Inbox: conversations and messages.

async def list_conversations(
    *, account_id: str | None = None, profile_id: str | None = None,
    platform: str | None = None, limit: int = 50, cursor: str | None = None,
) -> tuple[list[dict], str | None]:
    params: dict = {"limit": max(1, min(limit, 100))}
    if account_id:
        params["accountId"] = account_id
    if profile_id:
        params["profileId"] = profile_id
    if platform:
        params["platform"] = platform
    if cursor:
        params["cursor"] = cursor
    body = await _request("GET", "/inbox/conversations", params=params)
    items = body.get("data")
    pagination = body.get("pagination") or {}
    return items if isinstance(items, list) else [], pagination.get("nextCursor")


async def list_messages(
    account_id: str, conversation_id: str, *, limit: int = 50,
    cursor: str | None = None, sort_order: str = "asc",
) -> tuple[list[dict], dict]:
    params: dict = {"accountId": account_id, "limit": max(1, min(limit, 100)), "sortOrder": sort_order}
    if cursor:
        params["cursor"] = cursor
    body = await _request("GET", f"/inbox/conversations/{conversation_id}/messages", params=params)
    messages = body.get("messages")
    return messages if isinstance(messages, list) else [], body.get("pagination") or {}


async def send_message(
    account_id: str, conversation_id: str, *, message: str = "",
    attachment_url: str | None = None, attachment_type: str | None = None,
    attachment_name: str | None = None, voice_note: bool = False,
    reply_to: str | None = None, template: dict | None = None,
    messaging_type: str | None = None, message_tag: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    payload: dict = {"accountId": account_id}
    if message:
        payload["message"] = message
    if attachment_url:
        payload["attachmentUrl"] = attachment_url
        payload["attachmentType"] = attachment_type or "file"
        if attachment_name:
            payload["attachmentName"] = attachment_name
        if voice_note:
            payload["voiceNote"] = True
    if reply_to:
        payload["replyTo"] = reply_to
    if template:
        payload["template"] = template
    if messaging_type:
        payload["messagingType"] = messaging_type
    if message_tag:
        payload["messageTag"] = message_tag
    status, body = await _request_with_status(
        "POST", f"/inbox/conversations/{conversation_id}/messages",
        json=payload, idempotency_key=idempotency_key)
    if status >= 400:
        raise HTTPException(status_code=502, detail=f"The message was not delivered: {_safe_error(body, status)}")
    data = body.get("data") or {}
    if not isinstance(data, dict) or not data.get("messageId"):
        raise HTTPException(status_code=502, detail="The messaging provider did not confirm delivery.")
    return data


async def create_conversation(
    account_id: str, participant_id: str, *, message: str = "",
    template_name: str | None = None, template_language: str | None = None,
    template_params: list | None = None, template_button_params: list | None = None,
    header_media: dict | None = None, header_location: dict | None = None,
    category: str | None = None,
) -> dict:
    payload: dict = {"accountId": account_id, "participantId": participant_id}
    if message:
        payload["message"] = message
    if template_name:
        payload["templateName"] = template_name
        if template_language:
            payload["templateLanguage"] = template_language
        if template_params:
            payload["templateParams"] = template_params
    if template_button_params:
        payload["templateButtonParams"] = template_button_params
    if header_media:
        payload["headerMedia"] = header_media
    if header_location:
        payload["headerLocation"] = header_location
    if category:
        payload["category"] = category
    status, body = await _request_with_status("POST", "/inbox/conversations", json=payload)
    if status >= 400:
        raise HTTPException(status_code=502, detail=f"The conversation was not started: {_safe_error(body, status)}")
    data = body.get("data") or {}
    if not isinstance(data, dict) or not data.get("conversationId"):
        raise HTTPException(status_code=502, detail="The messaging provider did not confirm delivery.")
    return data


async def send_media_upload(
    account_id: str, conversation_id: str, *, data: bytes, mime: str, filename: str,
    message: str = "", voice_note: bool = False, reply_to: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Send a file from bytes (multipart upload). Voice recordings ride with
    the voice-note flag so they render as playable voice messages."""
    require_config()
    form: dict = {"accountId": account_id}
    if message:
        form["message"] = message
    if reply_to:
        form["replyTo"] = reply_to
    if voice_note:
        form["voiceNote"] = "true"
    files = {"attachment": (filename or "file.bin", data, mime or "application/octet-stream")}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                _url(f"/inbox/conversations/{conversation_id}/messages"),
                headers=_headers(idempotency_key=idempotency_key),
                data=form, files=files)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach the messaging provider.") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=502, detail="The messaging provider returned an invalid response.")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"The file was not delivered: {_safe_error(body, response.status_code)}")
    data_body = body.get("data") or {}
    if not isinstance(data_body, dict) or not data_body.get("messageId"):
        raise HTTPException(status_code=502, detail="The messaging provider did not confirm delivery.")
    return data_body


async def mark_read(account_id: str, conversation_id: str) -> None:
    status, body = await _request_with_status(
        "POST", f"/inbox/conversations/{conversation_id}/read", json={"accountId": account_id})
    if status >= 400:
        raise HTTPException(status_code=502, detail=f"The conversation was not marked as read: {_safe_error(body, status)}")


async def send_typing(account_id: str, conversation_id: str) -> None:
    """Best-effort typing indicator; a gesture is never worth failing over."""
    try:
        await _request("POST", f"/inbox/conversations/{conversation_id}/typing", json={"accountId": account_id})
    except HTTPException:
        pass


async def send_reaction(account_id: str, conversation_id: str, platform_message_id: str, emoji: str) -> None:
    status, body = await _request_with_status(
        "POST", f"/inbox/conversations/{conversation_id}/messages/{platform_message_id}/reactions",
        json={"accountId": account_id, "emoji": emoji})
    if status >= 400:
        raise HTTPException(status_code=502, detail=f"The reaction was not delivered: {_safe_error(body, status)}")


async def remove_reaction(account_id: str, conversation_id: str, platform_message_id: str) -> None:
    status, body = await _request_with_status(
        "DELETE", f"/inbox/conversations/{conversation_id}/messages/{platform_message_id}/reactions",
        params={"accountId": account_id})
    if status >= 400:
        raise HTTPException(status_code=502, detail=f"The reaction was not removed: {_safe_error(body, status)}")


async def resolve_attachment(account_id: str, conversation_id: str, platform_message_id: str, index: int) -> tuple[bytes, str]:
    """Download an attachment through the resolve endpoint (re-mints expiring links)."""
    require_config()
    url = _url(f"/inbox/conversations/{conversation_id}/messages/{platform_message_id}/attachments/{index}")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url, headers=_headers(), params={"accountId": account_id})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not download the attachment.") from exc
    if response.status_code >= 400 or not response.content:
        raise HTTPException(status_code=502, detail="The attachment is unavailable or expired.")
    mime = (response.headers.get("content-type") or "application/octet-stream").split(";")[0].strip().lower()
    return response.content, mime


async def fetch_media(url: str) -> tuple[bytes, str]:
    """Download an inbound media file. Provider-hosted links need the key;
    platform CDN links ignore the extra header."""
    require_config()
    if not url.startswith("https://"):
        raise HTTPException(status_code=502, detail="The attachment URL is not supported.")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            async with client.stream("GET", url, headers={**_headers(), "Accept-Encoding": "identity"}) as response:
                if response.status_code != 200:
                    raise HTTPException(status_code=502, detail="The shared media is unavailable or expired.")
                mime = (response.headers.get("content-type") or "application/octet-stream").split(";")[0].strip().lower()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > MAX_MEDIA_BYTES:
                        raise HTTPException(status_code=413, detail="The attachment exceeds the 20 MB limit.")
                    data.extend(chunk)
                if not data:
                    raise HTTPException(status_code=502, detail="The shared media is empty or unavailable.")
                return bytes(data), mime
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not download the attachment.") from exc


# WhatsApp templates (approved templates open or re-engage conversations).

async def list_templates(account_id: str, *, name: str | None = None,
                         language: str | None = None, status: str | None = None) -> list[dict]:
    params: dict = {"accountId": account_id}
    if name:
        params["name"] = name
    if language:
        params["language"] = language
    if status:
        params["status"] = status
    body = await _request("GET", "/whatsapp/templates", params=params)
    templates = body.get("templates")
    return templates if isinstance(templates, list) else []


async def get_template(account_id: str, name: str, language: str | None = None) -> dict:
    params: dict = {"accountId": account_id}
    if language:
        params["language"] = language
    body = await _request("GET", f"/whatsapp/templates/{name}", params=params)
    template = body.get("template")
    if not isinstance(template, dict):
        raise HTTPException(status_code=502, detail="The messaging provider did not return the template.")
    return template


async def create_template(account_id: str, payload: dict) -> dict:
    body = await _request("POST", "/whatsapp/templates", json={"accountId": account_id, **payload})
    template = body.get("template")
    if not isinstance(template, dict):
        raise HTTPException(status_code=502, detail="The messaging provider did not return the template.")
    return template


async def update_template(account_id: str, name: str, payload: dict, *, language: str | None = None) -> dict:
    params = {"language": language} if language else None
    body = await _request("PATCH", f"/whatsapp/templates/{name}",
                          params=params, json={"accountId": account_id, **payload})
    return body.get("template") if isinstance(body.get("template"), dict) else {}


async def delete_template(account_id: str, name: str, *, language: str | None = None) -> None:
    params: dict = {"accountId": account_id}
    if language:
        params["language"] = language
    status, body = await _request_with_status("DELETE", f"/whatsapp/templates/{name}", params=params)
    if status >= 400:
        raise HTTPException(status_code=502, detail=f"The template was not deleted: {_safe_error(body, status)}")


# Webhook subscriptions (one shared endpoint for every channel).

async def list_webhooks() -> list[dict]:
    body = await _request("GET", "/webhooks/settings")
    webhooks = body.get("webhooks")
    return webhooks if isinstance(webhooks, list) else []


async def create_webhook(name: str, url: str, secret: str, events: list[str]) -> dict:
    body = await _request("POST", "/webhooks/settings",
                          json={"name": name, "url": url, "secret": secret, "events": events})
    webhook = body.get("webhook")
    if not isinstance(webhook, dict):
        raise HTTPException(status_code=502, detail="The messaging provider did not return the webhook.")
    return webhook


async def update_webhook(webhook_id: str, payload: dict) -> dict:
    body = await _request("PUT", "/webhooks/settings", json={"_id": webhook_id, **payload})
    webhook = body.get("webhook")
    return webhook if isinstance(webhook, dict) else {}


async def ensure_webhook(name: str, url: str, secret: str, events: list[str]) -> dict:
    """Register the shared event endpoint, or repair it when it drifted."""
    for webhook in await list_webhooks():
        if not isinstance(webhook, dict):
            continue
        if webhook.get("url") == url or webhook.get("name") == name:
            current = set(webhook.get("events") or [])
            if current >= set(events) and webhook.get("isActive", True):
                return webhook
            return await update_webhook(str(webhook.get("_id") or webhook.get("id") or ""),
                                        {"url": url, "events": sorted(set(events) | current), "isActive": True})
    return await create_webhook(name, url, secret, events)


# Webhook signature (hex HMAC-SHA256 of the raw body keyed by the secret).

def signature_from_headers(headers) -> str:
    for name in _SIGNATURE_HEADERS:
        value = headers.get(name)
        if value:
            return value
    return ""


def verify_signature(raw: bytes, signature: str, secret: str) -> bool:
    if not raw or not signature or not secret:
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(signature.strip().encode("ascii"), expected.encode("ascii"))
    except (UnicodeEncodeError, ValueError):
        return False
