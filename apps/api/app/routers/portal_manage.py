"""The agency's agent screens, served to a client's own portal admins.

When the agency switches the ``agents`` function on for a client, that client's
portal admins get the same agent management the agency has: list, create, edit,
delete, knowledge, tools, escalation rules, the playground, the model catalog.
The web app reuses the agency's screens with one difference, the URL prefix
``/api/portal/{slug}/manage`` in front of the panel's own paths.

Nothing here reimplements a handler. Each route below is the panel's endpoint
function, mounted a second time with one change: the ``get_current_user``
dependency it declares is swapped for :func:`portal_actor`. That dependency is
the whole door, in one place:

* the portal session is authenticated the way every portal route does it,
* the ``agents`` function must be on for the client (403 otherwise, so turning
  it off cuts access at the next request),
* the person must hold ``agents.manage`` (admins only),
* and the request then acts for the client's agency, confined to that one
  client: the actor carries ``confined_client_id`` and the lookup helpers the
  handlers already use (``_agent``, ``_client``, ``_conversation``,
  ``_validate_client``) add it to their queries.

Only what the agent screens call is mounted (``MOUNTED``); a panel route not
named there stays closed to the portal. The agency's AI keys are never
reachable: the provider list is mounted stripped of everything but "is a key
available", and nothing that writes or tests a key is mounted at all.
"""

import functools
import inspect

from fastapi import APIRouter, Depends, HTTPException
from fastapi.params import Depends as DependsParam
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import PortalActor, get_current_user
from ..models import Client, PortalUser
from ..portal_features import ensure_enabled
from ..portal_permissions import AGENTS_MANAGE, has_permission
from ..schemas import ProviderOut
from . import agent_tools, agents, catalog, clients, conversations, providers
from .portal import _portal_client, _portal_user, require_feature

FEATURE = "agents"

router = APIRouter(prefix="/portal/{slug}/manage", tags=["Client portal: agents"])


def portal_actor(
    client: Client = Depends(_portal_client),
    user: PortalUser | None = Depends(_portal_user),
) -> PortalActor:
    """Who a mounted route acts as: this client's portal admin, nobody else.

    Order matters for the answers: no session is a 401 (``_portal_client``),
    then the function being off is a 403, then a role without the permission.
    """
    ensure_enabled(client, FEATURE)
    if not user or not has_permission(user.role, AGENTS_MANAGE):
        raise HTTPException(status_code=403, detail="Your role cannot do this")
    return PortalActor(
        agency_id=client.agency_id,
        id=user.id,
        client_id=client.id,
        name=user.name,
        email=user.email,
    )


def _as_portal(endpoint):
    """The same endpoint, asking for the portal actor where it asked for the user."""
    signature = inspect.signature(endpoint)
    swapped = []
    replaced = False
    for parameter in signature.parameters.values():
        default = parameter.default
        if isinstance(default, DependsParam) and default.dependency is get_current_user:
            parameter = parameter.replace(default=Depends(portal_actor))
            replaced = True
        swapped.append(parameter)
    if not replaced:
        raise RuntimeError(f"{endpoint.__name__} does not take get_current_user; it cannot be mounted in the portal")

    if inspect.iscoroutinefunction(endpoint):
        @functools.wraps(endpoint)
        async def wrapper(*args, **kwargs):
            return await endpoint(*args, **kwargs)
    else:
        @functools.wraps(endpoint)
        def wrapper(*args, **kwargs):
            return endpoint(*args, **kwargs)
    wrapper.__signature__ = signature.replace(parameters=swapped)
    return wrapper


# (source router, the (METHOD, panel path) pairs to mount, or None for all of it)
MOUNTED: tuple[tuple[APIRouter, frozenset[tuple[str, str]] | None], ...] = (
    # Agents, their knowledge (documents and Q&A), prompt preview, escalation rules.
    (agents.router, None),
    # Custom tools: HTTP endpoints and MCP servers (with the connection test).
    (agent_tools.router, None),
    # What the escalation editor and the pickers need from the client itself.
    (clients.router, frozenset({
        ("GET", "/clients"),
        ("GET", "/clients/{client_id}/teams"),
        ("GET", "/clients/{client_id}/portal-users"),
        ("GET", "/clients/{client_id}/contact-tags"),
        ("PATCH", "/clients/{client_id}/contact-tags/{tag_id}"),
    })),
    # The playground: rehearsal threads with the agent, answered by the real model.
    (conversations.router, frozenset({
        ("GET", "/conversations"),
        ("POST", "/conversations"),
        ("GET", "/conversations/{conversation_id}"),
        ("DELETE", "/conversations/{conversation_id}"),
        ("POST", "/conversations/{conversation_id}/messages"),
        ("POST", "/conversations/{conversation_id}/media"),
        ("GET", "/conversations/{conversation_id}/attachments/{attachment_id}"),
    })),
    # Model names, context windows and prices: reference data, no secrets.
    (catalog.router, None),
)

# Extra functions a mounted route needs on top of ``agents``. Routing a tag to a
# team or person edits a tag, which the agency may have kept switched off.
EXTRA_FEATURES: dict[tuple[str, str], tuple[str, ...]] = {
    ("PATCH", "/clients/{client_id}/contact-tags/{tag_id}"): ("tags",),
}


def _mount() -> None:
    for source, only in MOUNTED:
        for route in source.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                if only is not None and (method, route.path) not in only:
                    continue
                router.add_api_route(
                    route.path,
                    _as_portal(route.endpoint),
                    methods=[method],
                    response_model=route.response_model,
                    status_code=route.status_code,
                    # The panel's scope guards answer API tokens; a portal session has none.
                    dependencies=[Depends(require_feature(key)) for key in EXTRA_FEATURES.get((method, route.path), ())],
                    name=route.name,
                )
    # The provider list, minus everything about the agency's keys.
    router.add_api_route("/providers", list_available_providers, methods=["GET"], response_model=list[ProviderOut])


def list_available_providers(db: Session = Depends(get_db), actor: PortalActor = Depends(portal_actor)):
    """Whether each AI provider can answer, which is all the playground asks.

    The panel's answer also carries the masked key and where it comes from;
    neither leaves the agency, so both are blanked here.
    """
    rows = providers.list_providers(db, actor)
    return [
        {**row, "api_key_masked": "", "source": "agency" if row["configured"] else "none"}
        for row in rows
    ]


_mount()
