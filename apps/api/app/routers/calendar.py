"""Google Calendar: agency-side administration and the public connection link.

A calendar member is created and managed from the agency's client page or the
client portal (both authenticated). The link a member authorizes with, and
Google's OAuth callback, need neither: the person on the other end of the
share link has no OpenLivery account.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi import HTTPException

from ..database import get_db
from ..deps import get_current_user
from ..models import Client, User
from ..schemas_calendar import (
    CalendarConnectInfoOut,
    CalendarConnectStartOut,
    CalendarEventsOut,
    CalendarMemberCreate,
    CalendarMemberOut,
    CalendarMemberUpdate,
    CalendarOverviewOut,
)
from ..services import calendar as calendar_service

router = APIRouter(tags=["Calendar"])


def _client(db: Session, user: User, client_id: uuid.UUID) -> Client:
    client = db.scalar(select(Client).where(Client.id == client_id, Client.agency_id == user.agency_id))
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


@router.get("/clients/{client_id}/calendar", response_model=CalendarOverviewOut)
def client_calendar(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return calendar_service.overview(db, _client(db, user, client_id))


@router.get("/clients/{client_id}/calendar/events", response_model=CalendarEventsOut)
async def client_calendar_events(
    client_id: uuid.UUID, start: datetime = Query(...), end: datetime = Query(...),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    return await calendar_service.events(db, _client(db, user, client_id), start, end)


@router.post("/clients/{client_id}/calendar/members", response_model=CalendarMemberOut, status_code=status.HTTP_201_CREATED)
def client_create_calendar_member(
    client_id: uuid.UUID, payload: CalendarMemberCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return calendar_service.member_out(calendar_service.create_member(db, _client(db, user, client_id), payload))


@router.patch("/clients/{client_id}/calendar/members/{member_id}", response_model=CalendarMemberOut)
def client_update_calendar_member(
    client_id: uuid.UUID, member_id: uuid.UUID, payload: CalendarMemberUpdate,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    return calendar_service.member_out(calendar_service.update_member(db, _client(db, user, client_id), member_id, payload))


@router.post("/clients/{client_id}/calendar/members/{member_id}/renew-link", response_model=CalendarMemberOut)
def client_renew_calendar_link(
    client_id: uuid.UUID, member_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return calendar_service.member_out(calendar_service.renew_link(db, _client(db, user, client_id), member_id))


@router.post("/clients/{client_id}/calendar/members/{member_id}/disconnect", response_model=CalendarMemberOut)
async def client_disconnect_calendar_member(
    client_id: uuid.UUID, member_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return calendar_service.member_out(await calendar_service.disconnect(db, _client(db, user, client_id), member_id))


@router.delete("/clients/{client_id}/calendar/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def client_delete_calendar_member(
    client_id: uuid.UUID, member_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    await calendar_service.delete_member(db, _client(db, user, client_id), member_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# The public connection link (no session: opened directly by the team member)
# and Google's OAuth callback for it.

@router.get("/calendar/connect/{token}", response_model=CalendarConnectInfoOut)
def calendar_connect_info(token: str, db: Session = Depends(get_db)):
    return calendar_service.link_info(db, token)


@router.post("/calendar/connect/{token}/start", response_model=CalendarConnectStartOut)
def calendar_connect_start(token: str, db: Session = Depends(get_db)):
    return {"authorization_url": calendar_service.start_connection(db, token)}


@router.get("/calendar/oauth/callback")
async def calendar_oauth_callback(
    state: str = Query(max_length=256), code: str | None = Query(default=None, max_length=8192),
    error: str | None = Query(default=None, max_length=256), db: Session = Depends(get_db),
):
    target = await calendar_service.finish_connection(db, state, code, error)
    return RedirectResponse(target)
