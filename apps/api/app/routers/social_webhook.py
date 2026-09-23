"""Expiring attachment delivery URLs for messaging channels."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Conversation, Message, MessageAttachment, SocialChannel, now_utc
from ..services.social_media import verify_media_signature
from ..services.attachments import content_disposition

public_router = APIRouter(prefix="/public/social", tags=["Social messaging callbacks"])


def _channel(db: Session, channel_id: uuid.UUID) -> SocialChannel:
    channel = db.get(SocialChannel, channel_id)
    if not channel:
        raise HTTPException(404, "Unknown channel")
    return channel


@public_router.get("/media/{channel_id}/{attachment_id}")
def media(channel_id: uuid.UUID, attachment_id: uuid.UUID, expires: int, signature: str,
          db: Session = Depends(get_db)):
    channel = _channel(db, channel_id)
    now = int(now_utc().timestamp())
    if (not channel.is_enabled or expires <= now or expires > now + 86400
            or not verify_media_signature(channel, attachment_id, expires, signature)):
        raise HTTPException(403, "This attachment link is invalid or expired")
    attachment = db.scalar(select(MessageAttachment).join(Message).join(Conversation).where(
        MessageAttachment.id == attachment_id, Conversation.social_channel_id == channel.id,
        Message.role == "assistant"))
    if not attachment:
        raise HTTPException(404, "Attachment not found")
    return Response(attachment.data, media_type=attachment.mime,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                 "Content-Security-Policy": "default-src 'none'; sandbox", "Referrer-Policy": "no-referrer",
                 "Content-Disposition": content_disposition(attachment.filename)})
