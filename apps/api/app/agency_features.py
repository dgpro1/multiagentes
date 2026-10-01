"""Which modules an agency may use, decided by the platform owner.

One level above the portal's per-client switches (app/portal_features.py).
Each level only restricts, never grants: a portal function is effective only
when the agency's module is on AND the client's own switch is on. The web app
keeps a typed mirror in ``apps/web/lib/agency-features.ts`` and a test keeps
the two lists equal.

Every module that existed when this landed defaults to on, so no existing
agency changed behaviour; a module added later may ship off (``agency_backend``). An agency stores only what the platform changed;
a missing key reads as its default, and presets are named snapshots applied
at creation or from the panel.
"""

from typing import Any

from fastapi import HTTPException

from .portal_features import KEYS as PORTAL_KEYS
from .portal_features import is_enabled as portal_is_on

FEATURE_DISABLED_DETAIL = "This module is not enabled for this agency"

# (key, default), in display order. All on: every module ships working and
# the platform narrows per agency.
CATALOG: tuple[tuple[str, bool], ...] = (
    ("clients", True),
    ("agents", True),
    ("inbox", True),
    ("playground", True),
    ("teams", True),
    ("templates", True),
    ("canned", True),
    ("channels.whatsapp", True),
    ("channels.whatsapp_cloud", True),
    ("channels.instagram", True),
    ("channels.messenger", True),
    ("channels.webchat", True),
    ("pipeline", True),
    ("calendar", True),
    ("appointments", True),
    ("professionals", True),
    ("services", True),
    ("knowledge", True),
    ("resources", True),
    ("storage", True),
    ("data_store", True),
    ("reports", True),
    ("integrations", True),
    ("branding", True),
    # The one module that ships off: the agency looks after its clients' data
    # in its own Supabase project (app/services/agency_backend.py), which only
    # the platform owner can allow.
    ("agency_backend", False),
)

KEYS: tuple[str, ...] = tuple(key for key, _ in CATALOG)
DEFAULTS: dict[str, bool] = dict(CATALOG)

# Which portal functions each module caps. A portal function that consumes no
# separately-managed module stays governed only by the agency's per-client
# switch (contacts, tags, details).
PORTAL_OF_MODULE: dict[str, tuple[str, ...]] = {
    "channels.whatsapp": ("channels.whatsapp",),
    "channels.whatsapp_cloud": ("channels.whatsapp_cloud",),
    "channels.instagram": ("channels.instagram",),
    "channels.messenger": ("channels.messenger",),
    "channels.webchat": ("channels.webchat",),
    "agents": ("agents",),
    "integrations": ("api",),
    "pipeline": ("pipeline",),
    "calendar": ("calendar", "appointments"),
    "professionals": ("professionals",),
    "services": ("services",),
    "resources": ("resources",),
    "inbox": ("inbox",),
    "teams": ("teams",),
    "templates": ("templates",),
    "canned": ("canned",),
    "reports": ("reports",),
}

MODULE_OF_PORTAL: dict[str, str] = {
    portal_key: module for module, portal_keys in PORTAL_OF_MODULE.items() for portal_key in portal_keys
}

# Named snapshots for the panel: applying one writes its keys into the agency.
PRESETS: dict[str, tuple[str, ...]] = {
    "starter": (
        "clients", "agents", "inbox", "playground", "teams", "templates", "canned",
        "channels.webchat", "pipeline", "calendar", "reports", "knowledge",
    ),
    "pro": tuple(key for key in KEYS if key not in ("storage", "data_store", "agency_backend")),
    "full": KEYS,
}


def defaults() -> dict[str, bool]:
    """A fresh copy of the defaults, safe to store or mutate."""
    return dict(DEFAULTS)


def normalize(stored: dict | None) -> dict[str, bool]:
    """The full set of switches: defaults, overridden by what is stored.
    Unknown keys are dropped and values coerced to bool."""
    result = defaults()
    if isinstance(stored, dict):
        for key in KEYS:
            if key in stored:
                result[key] = bool(stored[key])
    return result


def enabled_keys(agency: Any) -> list[str]:
    """The modules switched on for ``agency``, in catalog order."""
    features = normalize(getattr(agency, "features", None))
    return [key for key in KEYS if features[key]]


def is_enabled(agency: Any, key: str) -> bool:
    return normalize(getattr(agency, "features", None)).get(key, False)


def validate_patch(patch: dict) -> dict[str, bool]:
    """Check a platform's partial update: known keys, real booleans."""
    if not isinstance(patch, dict):
        raise HTTPException(status_code=422, detail="features must be an object of switches")
    unknown = sorted(str(key) for key in patch if key not in DEFAULTS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown module: {', '.join(unknown)}")
    for key, value in patch.items():
        if not isinstance(value, bool):
            raise HTTPException(status_code=422, detail=f"Module '{key}' must be true or false")
    return {key: value for key, value in patch.items()}


def merged(stored: dict | None, patch: dict) -> dict[str, bool]:
    """The stored switches (normalized) with ``patch`` applied on top."""
    return {**normalize(stored), **validate_patch(patch)}


def ensure_enabled(agency: Any, key: str) -> None:
    if not is_enabled(agency, key):
        raise HTTPException(status_code=403, detail=FEATURE_DISABLED_DETAIL)


def effective_portal_keys(client: Any, agency: Any) -> list[str]:
    """The client's portal functions whose agency module is also on. The portal
    and mobile sessions carry this list, so the UI never offers what the
    ceiling refuses."""
    modules = set(enabled_keys(agency))
    return [
        key for key in PORTAL_KEYS
        if portal_is_on(client, key)
        and (MODULE_OF_PORTAL.get(key) is None or MODULE_OF_PORTAL[key] in modules)
    ]
