"""Google's OAuth and Calendar endpoints, and nothing else.

Every call here speaks to Google over HTTPS with the installation's single
OAuth client (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET). The lifecycle of a
connection, and what gets stored, lives in app/services/calendar.py.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import httpx
import jwt

from ..config import get_settings

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"

EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
FREEBUSY_SCOPE = "https://www.googleapis.com/auth/calendar.freebusy"
# Read and write events (the agent books appointments) and ask for free/busy
# windows; "openid email" names the account that authorized.
SCOPES = ("openid", "email", EVENTS_SCOPE, FREEBUSY_SCOPE)
TIMEOUT = 15


class GoogleError(Exception):
    """Google answered with an error. ``revoked`` means the stored grant is no
    longer valid and only a new authorization can fix it."""

    def __init__(self, message: str, revoked: bool = False):
        super().__init__(message)
        self.revoked = revoked


@dataclass(frozen=True)
class Grant:
    refresh_token: str
    access_token: str
    expires_at: datetime
    email: str | None
    scopes: frozenset[str]


def configured() -> bool:
    settings = get_settings()
    return bool(settings.google_client_id and settings.google_client_secret)


def redirect_uri() -> str:
    settings = get_settings()
    return settings.google_redirect_uri or f"{settings.frontend_url.rstrip('/')}/api/calendar/oauth/callback"


def authorization_url(state: str, login_hint: str | None = None) -> str:
    query = {
        "client_id": get_settings().google_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        # offline + consent: Google returns a refresh token every time, even to
        # an account that authorized before, so a reconnect always works.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    if login_hint:
        query["login_hint"] = login_hint
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


def _expiry(payload: dict) -> datetime:
    # A minute early, so a token is never used in its last seconds.
    return datetime.now(timezone.utc) + timedelta(seconds=max(int(payload.get("expires_in") or 3600) - 60, 0))


async def _post_token(data: dict) -> dict:
    settings = get_settings()
    data = {**data, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(TOKEN_URL, data=data)
    except httpx.HTTPError as exc:
        raise GoogleError("Google could not be reached. Try again in a moment") from exc
    payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if response.status_code >= 400:
        error = payload.get("error", "")
        raise GoogleError(payload.get("error_description") or error or "Google refused the request", revoked=error == "invalid_grant")
    return payload


async def exchange_code(code: str) -> Grant:
    payload = await _post_token({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri()})
    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        raise GoogleError("Google did not grant offline access")
    email = None
    if payload.get("id_token"):
        # The token came straight from Google's token endpoint over TLS, which
        # OpenID Connect accepts in place of checking its signature.
        claims = jwt.decode(payload["id_token"], options={"verify_signature": False})
        email = claims.get("email")
    return Grant(
        refresh_token=refresh_token,
        access_token=payload["access_token"],
        expires_at=_expiry(payload),
        email=email,
        scopes=frozenset((payload.get("scope") or "").split()),
    )


async def refresh(refresh_token: str) -> tuple[str, datetime]:
    payload = await _post_token({"grant_type": "refresh_token", "refresh_token": refresh_token})
    return payload["access_token"], _expiry(payload)


async def revoke(token: str) -> None:
    """Best effort: a token Google already dropped is just as gone."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            await client.post(REVOKE_URL, data={"token": token})
    except httpx.HTTPError:
        pass


async def list_events(access_token: str, calendar_id: str, start: datetime, end: datetime) -> list[dict]:
    """Events overlapping [start, end), recurring ones expanded, in start order."""
    url = f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events"
    params = {
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": "250",
    }
    items: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            for _ in range(8):
                response = await client.get(url, params=params, headers={"Authorization": f"Bearer {access_token}"})
                if response.status_code == 401:
                    raise GoogleError("Google rejected the calendar access", revoked=True)
                if response.status_code >= 400:
                    raise GoogleError(response.json().get("error", {}).get("message") or "Google could not list the events")
                page = response.json()
                items.extend(page.get("items", []))
                if not page.get("nextPageToken"):
                    break
                params["pageToken"] = page["nextPageToken"]
    except httpx.HTTPError as exc:
        raise GoogleError("Google could not be reached. Try again in a moment") from exc
    return items


async def create_event(access_token: str, calendar_id: str, event_data: dict) -> dict:
    """Create a new event on the specified Google Calendar."""
    url = f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                url,
                json=event_data,
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            )
            if response.status_code == 401:
                raise GoogleError("Google rejected the calendar access", revoked=True)
            if response.status_code >= 400:
                raise GoogleError(response.json().get("error", {}).get("message") or "Google could not create the event")
            return response.json()
    except httpx.HTTPError as exc:
        raise GoogleError("Google could not be reached. Try again in a moment") from exc


async def delete_event(access_token: str, calendar_id: str, event_id: str) -> None:
    """Delete an event from the specified Google Calendar."""
    url = f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.delete(url, headers={"Authorization": f"Bearer {access_token}"})
            if response.status_code == 401:
                raise GoogleError("Google rejected the calendar access", revoked=True)
            if response.status_code not in (200, 204, 404):
                raise GoogleError(response.json().get("error", {}).get("message") or "Google could not delete the event")
    except httpx.HTTPError as exc:
        raise GoogleError("Google could not be reached. Try again in a moment") from exc
