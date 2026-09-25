"""Which functions exist inside a client's portal.

The agency decides this client by client. The catalog below is the single
source of truth for the keys and their defaults; the web app keeps a typed
mirror in ``apps/web/lib/portal-features.ts`` and a test fails if the two
drift. A client stores only what the agency chose; anything missing reads as
the default, so adding a key to the catalog later never needs a data
migration, and a key that leaves the catalog is ignored where it is stored.

The first nine switches govern screens the portal already has, so they default
to on: every existing client keeps seeing exactly what it saw before. The
rest belong to screens the agency opens up per client and default to off.

Features only govern the portal (``/portal/{slug}/...``). The agency panel and
the public API are never affected by them.
"""

from typing import Any

from fastapi import HTTPException

FEATURE_DISABLED_DETAIL = "This feature is not enabled for this portal"

# (key, default), in display order.
CATALOG: tuple[tuple[str, bool], ...] = (
    ("inbox", True),
    ("contacts", True),
    ("pipeline", True),
    ("calendar", True),
    ("reports", True),
    ("teams", True),
    ("tags", True),
    ("templates", True),
    ("canned", True),
    ("agents", False),
    ("api", False),
    ("channels.whatsapp", False),
    ("channels.whatsapp_cloud", False),
    ("channels.instagram", False),
    ("channels.messenger", False),
    ("channels.webchat", False),
    ("details", False),
    ("professionals", False),
    ("services", False),
    ("appointments", False),
)

KEYS: tuple[str, ...] = tuple(key for key, _ in CATALOG)
DEFAULTS: dict[str, bool] = dict(CATALOG)


def defaults() -> dict[str, bool]:
    """A fresh copy of the defaults, safe to store or mutate."""
    return dict(DEFAULTS)


def normalize(stored: dict | None) -> dict[str, bool]:
    """The full set of switches: defaults, overridden by what is stored.

    Unknown keys are dropped and values are coerced to bool, so whatever an
    older or hand-edited row holds can never leak into a response.
    """
    result = defaults()
    if isinstance(stored, dict):
        for key in KEYS:
            if key in stored:
                result[key] = bool(stored[key])
    return result


def enabled_keys(client: Any) -> list[str]:
    """The keys switched on for ``client``, in catalog order."""
    features = normalize(getattr(client, "portal_features", None))
    return [key for key in KEYS if features[key]]


def is_enabled(client: Any, key: str) -> bool:
    return normalize(getattr(client, "portal_features", None)).get(key, False)


def validate_patch(patch: dict) -> dict[str, bool]:
    """Check an agency's partial update: known keys, real booleans."""
    if not isinstance(patch, dict):
        raise HTTPException(status_code=422, detail="portal_features must be an object of switches")
    unknown = sorted(str(key) for key in patch if key not in DEFAULTS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown portal feature: {', '.join(unknown)}")
    for key, value in patch.items():
        if not isinstance(value, bool):
            raise HTTPException(status_code=422, detail=f"Portal feature '{key}' must be true or false")
    return {key: value for key, value in patch.items()}


def merged(stored: dict | None, patch: dict) -> dict[str, bool]:
    """The stored switches (normalized) with ``patch`` applied on top."""
    return {**normalize(stored), **validate_patch(patch)}


def ensure_enabled(client: Any, key: str) -> None:
    if not is_enabled(client, key):
        raise HTTPException(status_code=403, detail=FEATURE_DISABLED_DETAIL)
