"""Calendars of a client's team: who is on it, their Google connections, and
the events the calendar view shows.

Shared by the agency's client page and the client portal, which manage the
same rows from two doors (both resolve the client their own way and hand it
here), and by the public connection link, which a team member opens in their
own browser to authorize their Google Calendar without an OpenLivery account.
"""

import asyncio
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import CalendarMember, CalendarOAuthState, Client, new_public_id, now_utc
from ..schemas_calendar import CalendarMemberCreate, CalendarMemberUpdate
from ..security import decrypt_secret, encrypt_secret
from . import google_calendar as google

# Distinct on both themes, in the order new members take them.
PALETTE = ("#2f6df0", "#00a67d", "#7c5cff", "#d4932f", "#c83b82", "#0891b2", "#c43d4b", "#65a30d")
MAX_MEMBERS = 50
MAX_RANGE = timedelta(days=62)
STATE_MINUTES = 10


def _frontend() -> str:
    return get_settings().frontend_url.rstrip("/")


def connect_url(member: CalendarMember) -> str:
    return f"{_frontend()}/connect/calendar/{member.connect_token}"


def _link_expiry() -> datetime:
    return now_utc() + timedelta(days=get_settings().calendar_link_days)


def member_out(member: CalendarMember) -> dict:
    return {
        "id": member.id,
        "name": member.name,
        "role": member.role,
        "color": member.color,
        "status": member.status,
        "google_email": member.google_email,
        "connected_at": member.connected_at,
        "last_error": member.last_error,
        "connect_url": connect_url(member),
        "connect_expires_at": member.connect_expires_at,
        "link_expired": member.connect_expires_at <= now_utc(),
    }


def list_members(db: Session, client: Client) -> list[CalendarMember]:
    return db.scalars(
        select(CalendarMember).where(CalendarMember.client_id == client.id).order_by(CalendarMember.created_at)
    ).all()


def overview(db: Session, client: Client) -> dict:
    return {
        "oauth_ready": google.configured(),
        "timezone": client.timezone or "UTC",
        "members": [member_out(member) for member in list_members(db, client)],
    }


def get_member(db: Session, client: Client, member_id: uuid.UUID) -> CalendarMember:
    member = db.scalar(select(CalendarMember).where(CalendarMember.id == member_id, CalendarMember.client_id == client.id))
    if not member:
        raise HTTPException(status_code=404, detail="Calendar member not found")
    return member


def create_member(db: Session, client: Client, payload: CalendarMemberCreate) -> CalendarMember:
    existing = list_members(db, client)
    if len(existing) >= MAX_MEMBERS:
        raise HTTPException(status_code=409, detail=f"A client can have up to {MAX_MEMBERS} calendars")
    taken = {member.color for member in existing}
    color = payload.color or next((c for c in PALETTE if c not in taken), PALETTE[len(existing) % len(PALETTE)])
    member = CalendarMember(
        agency_id=client.agency_id,
        client_id=client.id,
        name=payload.name.strip(),
        role=payload.role.strip(),
        color=color,
        connect_expires_at=_link_expiry(),
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def update_member(db: Session, client: Client, member_id: uuid.UUID, payload: CalendarMemberUpdate) -> CalendarMember:
    member = get_member(db, client, member_id)
    if payload.name is not None:
        member.name = payload.name.strip()
    if payload.role is not None:
        member.role = payload.role.strip()
    if payload.color is not None:
        member.color = payload.color
    db.commit()
    db.refresh(member)
    return member


def renew_link(db: Session, client: Client, member_id: uuid.UUID) -> CalendarMember:
    """A fresh link; the previous one, and any authorization it started, stop working."""
    member = get_member(db, client, member_id)
    member.connect_token = new_public_id()
    member.connect_expires_at = _link_expiry()
    db.execute(delete(CalendarOAuthState).where(CalendarOAuthState.member_id == member.id))
    db.commit()
    db.refresh(member)
    return member


def _still_used(db: Session, member: CalendarMember, email: str | None) -> bool:
    """Whether another calendar of the agency runs on the same Google account.

    Google revokes a whole grant, not one token: revoking for one member would
    cut off every other member connected with that account.
    """
    if not email:
        return False
    return db.scalar(select(CalendarMember.id).where(
        CalendarMember.agency_id == member.agency_id, CalendarMember.id != member.id,
        CalendarMember.google_email == email, CalendarMember.status != "pending",
    ).limit(1)) is not None


def _forget_grant(db: Session, member: CalendarMember) -> str | None:
    """Clear the member's connection; the refresh token to revoke, if nobody else needs it."""
    token = decrypt_secret(member.encrypted_refresh_token) if member.encrypted_refresh_token else None
    if _still_used(db, member, member.google_email):
        token = None
    member.encrypted_refresh_token = None
    member.encrypted_access_token = None
    member.access_token_expires_at = None
    member.google_email = None
    member.connected_at = None
    member.last_error = None
    member.status = "pending"
    return token


async def disconnect(db: Session, client: Client, member_id: uuid.UUID) -> CalendarMember:
    member = get_member(db, client, member_id)
    token = _forget_grant(db, member)
    db.commit()
    db.refresh(member)
    if token:
        await google.revoke(token)
    return member


async def delete_member(db: Session, client: Client, member_id: uuid.UUID) -> None:
    member = get_member(db, client, member_id)
    token = _forget_grant(db, member)
    db.delete(member)
    db.commit()
    if token:
        await google.revoke(token)


async def access_token(db: Session, member: CalendarMember) -> str:
    """A live access token for the member's calendar, refreshed when due.

    The caller commits: a refresh, or the error state a revoked grant leaves
    behind, is written on the member and needs saving.
    """
    if member.status != "connected" or not member.encrypted_refresh_token:
        raise google.GoogleError("This calendar is not connected")
    if member.encrypted_access_token and member.access_token_expires_at and member.access_token_expires_at > now_utc():
        return decrypt_secret(member.encrypted_access_token)
    try:
        token, expires_at = await google.refresh(decrypt_secret(member.encrypted_refresh_token))
    except google.GoogleError as exc:
        if exc.revoked:
            member.status = "error"
            member.last_error = "Google access was revoked or expired. Connect the calendar again"
            member.encrypted_access_token = None
            member.access_token_expires_at = None
        raise
    member.encrypted_access_token = encrypt_secret(token)
    member.access_token_expires_at = expires_at
    return token


def _event_out(member: CalendarMember, item: dict) -> dict | None:
    if item.get("status") == "cancelled":
        return None
    start, end = item.get("start") or {}, item.get("end") or {}
    all_day = "date" in start and "dateTime" not in start
    start_value = start.get("date") if all_day else start.get("dateTime")
    end_value = end.get("date") if all_day else end.get("dateTime")
    if not start_value or not end_value:
        return None
    return {
        "id": f"{member.id}:{item.get('id', '')}",
        "member_id": member.id,
        "title": item.get("summary") or "",
        "start": start_value,
        "end": end_value,
        "all_day": all_day,
        "location": item.get("location") or "",
        "url": item.get("htmlLink") or "",
    }


async def events(db: Session, client: Client, start: datetime, end: datetime) -> dict:
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(status_code=422, detail="Give start and end with a timezone offset")
    if end <= start or end - start > MAX_RANGE:
        raise HTTPException(status_code=422, detail="The range must be positive and at most 62 days")
    members = [member for member in list_members(db, client) if member.status == "connected"]

    async def one(member: CalendarMember):
        try:
            token = await access_token(db, member)
            items = await google.list_events(token, member.calendar_id, start, end)
        except google.GoogleError as exc:
            if exc.revoked and member.status == "connected":
                member.status = "error"
                member.last_error = "Google access was revoked or expired. Connect the calendar again"
            return [], {"member_id": member.id, "detail": str(exc)}
        return [event for item in items if (event := _event_out(member, item))], None

    results = await asyncio.gather(*(one(member) for member in members))
    db.commit()
    return {
        "events": [event for found, _ in results for event in found],
        "errors": [error for _, error in results if error],
    }


# The public connection link.


def member_by_link(db: Session, token: str) -> CalendarMember:
    member = db.scalar(select(CalendarMember).where(CalendarMember.connect_token == token)) if 16 <= len(token) <= 64 else None
    if not member:
        raise HTTPException(status_code=404, detail="This link is not valid. Ask for a new one")
    return member


def link_info(db: Session, token: str) -> dict:
    member = member_by_link(db, token)
    client = member.client
    return {
        "member_name": member.name,
        "member_role": member.role,
        "color": member.color,
        "client_name": client.name,
        "has_logo": bool(client.logo_mime),
        "status": member.status,
        "google_email": member.google_email,
        "oauth_ready": google.configured(),
        "expired": member.connect_expires_at <= now_utc(),
    }


def start_connection(db: Session, token: str) -> str:
    member = member_by_link(db, token)
    if member.connect_expires_at <= now_utc():
        raise HTTPException(status_code=410, detail="This link has expired. Ask for a new one")
    if not google.configured():
        raise HTTPException(status_code=503, detail="Google Calendar is not configured on this server yet")
    # Housekeeping: states nobody came back with.
    db.execute(delete(CalendarOAuthState).where(CalendarOAuthState.expires_at < now_utc() - timedelta(days=1)))
    raw = secrets.token_urlsafe(32)
    db.add(CalendarOAuthState(
        id=hashlib.sha256(raw.encode()).hexdigest(),
        member_id=member.id,
        connect_token=member.connect_token,
        expires_at=now_utc() + timedelta(minutes=STATE_MINUTES),
    ))
    db.commit()
    return google.authorization_url(raw, login_hint=member.google_email)


async def finish_connection(db: Session, raw_state: str, code: str | None, error: str | None) -> str:
    """Where to send the browser back to, with the outcome in the query."""
    state = db.scalar(
        select(CalendarOAuthState).where(CalendarOAuthState.id == hashlib.sha256(raw_state.encode()).hexdigest()).with_for_update()
    ) if raw_state and len(raw_state) <= 256 else None
    if not state or state.used_at or state.expires_at <= now_utc():
        return f"{_frontend()}/connect/calendar/expired?result=expired"
    state.used_at = now_utc()
    db.commit()
    back = f"{_frontend()}/connect/calendar/{state.connect_token}"
    member = db.get(CalendarMember, state.member_id)
    if not member or member.connect_token != state.connect_token or member.connect_expires_at <= now_utc():
        return f"{back}?result=expired"
    if error or not code:
        return f"{back}?result=denied"
    try:
        grant = await google.exchange_code(code)
    except google.GoogleError:
        return f"{back}?result=error"
    if google.EVENTS_SCOPE not in grant.scopes:
        # The consent screen lets a person untick the calendar box. Nothing is
        # stored; the grant is left alone, since the account may serve another
        # calendar here.
        return f"{back}?result=scope"
    # A different account replaces the previous one, whose grant goes; the
    # same account's new grant supersedes the old one on Google's side already.
    previous = None
    if member.encrypted_refresh_token and member.google_email != grant.email and not _still_used(db, member, member.google_email):
        previous = decrypt_secret(member.encrypted_refresh_token)
    member.encrypted_refresh_token = encrypt_secret(grant.refresh_token)
    member.encrypted_access_token = encrypt_secret(grant.access_token)
    member.access_token_expires_at = grant.expires_at
    member.google_email = grant.email
    member.status = "connected"
    member.last_error = None
    member.connected_at = now_utc()
    db.commit()
    if previous:
        await google.revoke(previous)
    return f"{back}?result=connected"
