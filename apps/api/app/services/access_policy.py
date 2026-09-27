"""The one answer to "may this agency's credentials work right now".

Blocking access denies sessions, logins, API tokens, portals, mobile sessions,
OAuth grants and pending invitations. It deliberately does not touch channels,
messaging, scheduled messages, data stores or tenant upgrades: a blocked
agency's service keeps running, its people cannot reach it.
"""

from fastapi import HTTPException

BLOCKED_DETAIL = "This agency's access is blocked"


def blocked(agency) -> bool:
    return agency is not None and getattr(agency, "access_status", "active") != "active"


def ensure_agency_active(agency) -> None:
    """Raise for a blocked agency, so every credential gate shares one policy."""
    if blocked(agency):
        raise HTTPException(status_code=403, detail=BLOCKED_DETAIL)
