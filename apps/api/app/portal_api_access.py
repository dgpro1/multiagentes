"""What a client's portal admins may hand out through the API screen.

When the agency switches the ``api`` portal function on for a client, that
client's admins manage API integrations for their own client from the portal,
with the agency's own screen (``routers/integrations.py`` mounted by
``routers/portal_manage.py``). Handing out credentials is the most sensitive
thing the portal does, so it is narrower than the agency's screen in three ways
that live here and in the lookup helpers of that router:

* an integration made from the portal is always confined to the portal's own
  client, and no other row is ever visible to it;
* it may hold only scopes for things the client's portal has a screen for, and
  only while that portal function is on;
* the number of integrations, tokens and webhook subscriptions is capped.

Agency-level scopes (managing clients, credentials, webhooks and channels, and
writing agents, their knowledge or their tools) are never in the set: default
deny. Turning a portal function off later does not rewrite what was already
issued; it only stops the portal from issuing more of it.
"""

from fastapi import HTTPException

from .api_scopes import (
    AGENTS_READ,
    CALENDAR_MANAGE,
    CALENDAR_READ,
    CANNED_MANAGE,
    CHANNELS_READ,
    CONTACTS_MANAGE,
    CONTACTS_READ,
    INBOX_MANAGE,
    INBOX_READ,
    INBOX_REPLY,
    LEAD_FIELDS_MANAGE,
    LEAD_FIELDS_READ,
    PIPELINE_MANAGE,
    PIPELINE_READ,
    PRESETS,
    PROFESSIONALS_MANAGE,
    PROFESSIONALS_READ,
    REPORTS_READ,
    TAGS_MANAGE,
    TAGS_READ,
    TEAMS_MANAGE,
    TEAMS_READ,
    TEMPLATES_MANAGE,
    TEMPLATES_READ,
    resolve,
)

MAX_INTEGRATIONS = 10
MAX_TOKENS = 20
MAX_WEBHOOKS = 10

CHANNEL_FEATURES = (
    "channels.whatsapp",
    "channels.whatsapp_cloud",
    "channels.instagram",
    "channels.messenger",
    "channels.webchat",
)

# scope -> the portal functions that make it available (any one of them).
SCOPE_FEATURES: dict[str, tuple[str, ...]] = {
    INBOX_READ: ("inbox",),
    INBOX_REPLY: ("inbox",),
    INBOX_MANAGE: ("inbox",),
    CONTACTS_READ: ("contacts",),
    CONTACTS_MANAGE: ("contacts",),
    TAGS_READ: ("tags",),
    TAGS_MANAGE: ("tags",),
    TEAMS_READ: ("teams",),
    TEAMS_MANAGE: ("teams",),
    TEMPLATES_READ: ("templates",),
    TEMPLATES_MANAGE: ("templates",),
    CANNED_MANAGE: ("canned",),
    PIPELINE_READ: ("pipeline",),
    PIPELINE_MANAGE: ("pipeline",),
    CALENDAR_READ: ("calendar",),
    CALENDAR_MANAGE: ("calendar",),
    REPORTS_READ: ("reports",),
    PROFESSIONALS_READ: ("professionals",),
    PROFESSIONALS_MANAGE: ("professionals",),
    LEAD_FIELDS_READ: ("inbox",),
    LEAD_FIELDS_MANAGE: ("inbox",),
    # The agent's configuration is the agency's work unless it opened that screen.
    AGENTS_READ: ("agents",),
    CHANNELS_READ: CHANNEL_FEATURES,
}


def allowed_scopes(features) -> frozenset[str]:
    """The scopes a portal with these functions on may hand out."""
    on = set(features)
    return frozenset(scope for scope, needed in SCOPE_FEATURES.items() if on.intersection(needed))


def catalogue(features) -> tuple[frozenset[str], dict[str, list[str]]]:
    """The scopes and presets the portal's screen offers: the presets narrowed
    to what is allowed, ``full`` and any preset left empty dropped."""
    allowed = allowed_scopes(features)
    presets = {
        name: sorted(keys & allowed)
        for name, keys in PRESETS.items()
        if name != "full" and keys & allowed
    }
    return allowed, presets


def resolve_for_portal(features, preset: str, scopes: list[str] | None) -> list[str]:
    """The scopes a portal-made integration holds. An explicit list must be
    entirely allowed (422 otherwise); a preset is narrowed to what is allowed,
    except ``full``, which is refused."""
    allowed = allowed_scopes(features)
    if scopes:
        wanted = resolve("", scopes)  # unknown keys: ValueError, like the panel
        denied = sorted(set(wanted) - allowed)
        if denied:
            raise HTTPException(status_code=422, detail=f"The portal cannot grant: {', '.join(denied)}")
        return wanted
    if preset == "full":
        raise HTTPException(status_code=422, detail="The portal cannot grant the full preset")
    kept = sorted(set(resolve(preset, None)) & allowed)
    if not kept:
        raise HTTPException(status_code=422, detail="None of those scopes is available to this portal")
    return kept
