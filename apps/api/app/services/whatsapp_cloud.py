"""WhatsApp numbers served through the unified messaging provider.

Each channel keeps only its provider-side account id; the server key lives
in the environment. Sends address the provider conversation stored on each
case, falling back to opening by phone number when no thread is known yet.
Errors surface as HTTPException with safe messages (never credentials).
"""

from fastapi import HTTPException

from . import messaging_provider as provider
from .whatsapp_format import markdown_to_whatsapp

MAX_MEDIA_BYTES = 20 * 1024 * 1024
# Hard limit of WhatsApp for a text message body.
MAX_TEXT_LENGTH = 4096


async def verify_account(account_id: str, profile_id: str | None = None) -> dict:
    """Confirm the number is connected and return its public profile."""
    account = await provider.require_account(account_id, profile_id)
    raw = account.get("raw") or {}
    phone = account.get("phone_number") or raw.get("phoneNumber") or raw.get("username") or ""
    return {
        "display_phone_number": phone,
        "verified_name": account.get("display_name") or phone,
        "quality_rating": raw.get("qualityRating"),
        "messaging_limit": raw.get("messagingLimitTier") or raw.get("messaging_limit"),
        "username": phone,
    }


async def send_text(
    account_id: str, conversation_id: str, body: str, context_message_id: str | None = None
) -> str | None:
    """Send a text message; returns the provider message id.

    ``context_message_id`` makes it a quoted reply (the swipe-to-reply look)
    on the referenced platform message."""
    data = await provider.send_message(
        account_id, conversation_id,
        message=markdown_to_whatsapp(body)[:MAX_TEXT_LENGTH],
        reply_to=context_message_id,
    )
    return data.get("messageId")


async def open_conversation(
    account_id: str, phone: str, *, message: str = "",
    template_name: str | None = None, template_language: str | None = None,
    template_params: list | None = None,
) -> dict:
    """Start (or reuse) the thread with a phone number, by template or, when
    the window allows it, by text. Returns the provider conversation payload."""
    digits = "".join(char for char in (phone or "") if char.isdigit())
    if not digits:
        raise HTTPException(status_code=409, detail="This conversation does not have a valid WhatsApp destination")
    return await provider.create_conversation(
        account_id, digits, message=message[:MAX_TEXT_LENGTH] if message else "",
        template_name=template_name, template_language=template_language,
        template_params=template_params,
    )


async def send_reaction(account_id: str, conversation_id: str, message_id: str, emoji: str) -> None:
    """React with an emoji to a message; an empty emoji removes the reaction.
    Raises on failure so the caller decides whether the gesture matters."""
    if emoji:
        await provider.send_reaction(account_id, conversation_id, message_id, emoji)
    else:
        await provider.remove_reaction(account_id, conversation_id, message_id)


async def mark_read(account_id: str, conversation_id: str, message_id: str | None = None) -> None:
    """Mark the conversation as read (blue ticks). Best-effort, like the
    fused variant below."""
    try:
        await provider.mark_read(account_id, conversation_id)
    except HTTPException:
        pass


async def mark_read_with_typing(account_id: str, conversation_id: str, message_id: str | None = None) -> None:
    """Mark the conversation as read and show the typing indicator while the
    reply is being generated. Best-effort: the reply must never depend on it."""
    try:
        await provider.mark_read(account_id, conversation_id)
    except HTTPException:
        pass
    await provider.send_typing(account_id, conversation_id)


async def send_media(
    account_id: str,
    conversation_id: str,
    *,
    data: bytes,
    mime: str,
    filename: str,
    caption: str = "",
    voice_note: bool = False,
    reply_to: str | None = None,
) -> str | None:
    """Send an image/audio/video/file message from bytes; returns the provider
    message id. Audio captions ride as a follow-up text, which has no caption
    field of its own."""
    is_audio = (mime or "").lower().startswith("audio/")
    data_body = await provider.send_media_upload(
        account_id, conversation_id, data=data, mime=mime, filename=filename or "file.bin",
        message="" if is_audio else markdown_to_whatsapp(caption)[:1024],
        voice_note=voice_note and is_audio,
        reply_to=reply_to,
    )
    external_id = data_body.get("messageId")
    if caption and is_audio:
        await provider.send_message(account_id, conversation_id, message=markdown_to_whatsapp(caption)[:MAX_TEXT_LENGTH])
    return external_id


async def send_template(
    account_id: str, conversation_id: str, *, name: str, language: str, components: list[dict]
) -> str | None:
    """Send an approved template inside its conversation (re-engagement after
    the 24-hour window closed). Returns the provider message id."""
    data = await provider.send_message(
        account_id, conversation_id,
        template={"elements": [{"name": name, "language": language, "components": components or []}]},
    )
    return data.get("messageId")


async def fetch_media(url: str) -> tuple[bytes, str]:
    """Download an inbound media file from its provider URL."""
    data, mime = await provider.fetch_media(url)
    if len(data) > MAX_MEDIA_BYTES:
        raise HTTPException(status_code=502, detail="Could not download the media file.")
    return data, mime
