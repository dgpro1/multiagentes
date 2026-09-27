"""Supabase Management API, as a client's OAuth grant lets us use it.

Only what connecting a client's project needs: the OAuth2 code flow with PKCE,
listing the projects the person can see, running SQL on the chosen one (to
create OpenLivery's own database role), and reading its pooler settings to
build the connection string. The Management API never hands out the database
password, which is why a dedicated role with its own password is created.
"""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from ..config import get_settings
from ..models import now_utc

TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class SupabaseError(Exception):
    """A Management API call failed; the message is safe to show a person."""


@dataclass
class Grant:
    access_token: str
    refresh_token: str
    expires_at: datetime


def configured() -> bool:
    settings = get_settings()
    return bool(settings.supabase_oauth_client_id and settings.supabase_oauth_client_secret)


def _api() -> str:
    return get_settings().supabase_api_url.rstrip("/")


def redirect_uri() -> str:
    settings = get_settings()
    return settings.supabase_redirect_uri or f"{settings.frontend_url.rstrip('/')}/api/supabase/oauth/callback"


def new_verifier() -> str:
    return secrets.token_urlsafe(64)


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorization_url(state: str, verifier: str) -> str:
    query = {
        "client_id": get_settings().supabase_oauth_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "state": state,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
    }
    return f"{_api()}/v1/oauth/authorize?{urlencode(query)}"


async def _token(data: dict) -> Grant:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as http:
            response = await http.post(
                f"{_api()}/v1/oauth/token", data=data,
                auth=(settings.supabase_oauth_client_id, settings.supabase_oauth_client_secret),
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise SupabaseError("Could not reach Supabase") from exc
    if response.status_code >= 400:
        raise SupabaseError("Supabase refused the authorization")
    payload = response.json()
    return Grant(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token") or data.get("refresh_token", ""),
        expires_at=now_utc() + timedelta(seconds=int(payload.get("expires_in") or 3600) - 60),
    )


async def exchange_code(code: str, verifier: str) -> Grant:
    return await _token({
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": redirect_uri(), "code_verifier": verifier,
    })


async def refresh(refresh_token: str) -> Grant:
    return await _token({"grant_type": "refresh_token", "refresh_token": refresh_token})


async def _call(method: str, path: str, access_token: str, json: dict | None = None):
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as http:
            response = await http.request(
                method, f"{_api()}{path}", json=json,
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise SupabaseError("Could not reach Supabase") from exc
    if response.status_code in (401, 403):
        raise SupabaseError("Supabase did not grant access to that project; authorize again with the database permission")
    if response.status_code >= 400:
        # The SQL endpoint explains a failed statement in "message"; pass it on.
        try:
            detail = str((response.json() or {}).get("message") or "")[:300]
        except ValueError:
            detail = ""
        raise SupabaseError(f"Supabase answered {response.status_code}" + (f": {detail}" if detail else ""))
    return response.json() if response.content else None


async def list_projects(access_token: str) -> list[dict]:
    projects = await _call("GET", "/v1/projects", access_token) or []
    return [
        {"ref": p.get("id") or p.get("ref"), "name": p.get("name") or "", "region": p.get("region") or "",
         "status": p.get("status") or ""}
        for p in projects if (p.get("id") or p.get("ref"))
    ]


async def run_query(access_token: str, ref: str, query: str) -> object:
    return await _call("POST", f"/v1/projects/{ref}/database/query", access_token, json={"query": query})


async def pooler_config(access_token: str, ref: str) -> dict:
    """The primary database's pooler settings (the API answers a list, one per database)."""
    payload = await _call("GET", f"/v1/projects/{ref}/config/database/pooler", access_token)
    entries = payload if isinstance(payload, list) else [payload] if payload else []
    primary = next((e for e in entries if (e or {}).get("database_type", "PRIMARY") == "PRIMARY"), None)
    if not primary:
        raise SupabaseError("The project has no connection pooler configured")
    return primary
