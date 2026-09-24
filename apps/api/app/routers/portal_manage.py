"""The agency's agent and channel screens, served to a client's own portal admins.

When the agency switches the ``agents`` function on for a client, that client's
portal admins get the same agent management the agency has: list, create, edit,
delete, knowledge, tools, escalation rules, the playground, the model catalog.
When it switches a channel type on (``channels.whatsapp``,
``channels.whatsapp_cloud``, ``channels.instagram``, ``channels.messenger``,
``channels.webchat``) they get the agency's connect, configure and disconnect
screens for that type's lines. The web app reuses the agency's screens with one
difference, the URL prefix ``/api/portal/{slug}/manage`` in front of the panel's
own paths.

Nothing here reimplements a handler. Each route below is the panel's endpoint
function, mounted a second time with one change: the ``get_current_user``
dependency it declares is swapped for a portal actor. The actor dependency is
the whole door, and each mounted group has its own (:func:`_door`):

* the portal session is authenticated the way every portal route does it,
* the function(s) the route belongs to must be on for the client (403 otherwise,
  so turning one off cuts access at the next request; the lines themselves are
  never touched, only the portal's ability to manage them goes),
* the person must hold the group's permission (``agents.manage`` or
  ``channels.manage``, admins only),
* and the request then acts for the client's agency, confined to that one
  client: the actor carries ``confined_client_id`` and the lookup helpers the
  handlers already use (``_agent``, ``_client``, ``_conversation``,
  ``_validate_client``, ``_channel_for_user``, ``_owned_client``,
  ``owned_channel`` ...) add it to their queries.

A function is switched per type, never per line: with two WhatsApp lines, the
WhatsApp type opens both. Only what the screens call is mounted (``MOUNTED``); a
panel route not named there stays closed to the portal. The agency's AI keys are
never reachable (the provider list is mounted stripped of everything but "is a
key available", and nothing that writes or tests a key is mounted at all), and
provider identifiers and tokens a channel screen does not draw are blanked on
the way out (``_hide_*``).
"""

import functools
import inspect
from typing import Callable, NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.params import Depends as DependsParam
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import PortalActor, get_current_user
from ..models import Client, PortalUser
from ..portal_features import FEATURE_DISABLED_DETAIL, enabled_keys, ensure_enabled, is_enabled
from ..portal_permissions import AGENTS_MANAGE, CHANNELS_MANAGE, has_permission
from ..schemas import ProviderOut
from . import agent_tools, agents, catalog, clients, conversations, providers, social, webchat, whatsapp, whatsapp_cloud
from .portal import _portal_client, _portal_user, require_feature

FEATURE = "agents"
CHANNEL_FEATURES = (
    "channels.whatsapp",
    "channels.whatsapp_cloud",
    "channels.instagram",
    "channels.messenger",
    "channels.webchat",
)
# The ``/social/{provider}`` names and the channel type each one is.
SOCIAL_FEATURES = {"instagram": "channels.instagram", "messenger": "channels.messenger"}

router = APIRouter(prefix="/portal/{slug}/manage", tags=["Client portal: agents and channels"])

# Every actor dependency this module hands out; the tests check that each mounted
# route asks for one of them and never for the agency's own session.
ACTORS: set[Callable] = set()


def _door(permission: str, *, all_of: tuple[str, ...] = (), any_of: tuple[str, ...] = (), by_provider: bool = False) -> Callable:
    """Who a mounted route acts as: this client's portal admin, nobody else.

    Every function of ``all_of`` must be on, at least one of ``any_of``, and
    ``by_provider`` reads the type from the ``{provider}`` of the path
    (``/social/instagram/...`` needs ``channels.instagram``).

    Order matters for the answers: no session is a 401 (``_portal_client``),
    then a function being off is a 403, then a role without the permission.
    """

    def actor(
        request: Request,
        client: Client = Depends(_portal_client),
        user: PortalUser | None = Depends(_portal_user),
    ) -> PortalActor:
        for key in all_of:
            ensure_enabled(client, key)
        if any_of and not any(is_enabled(client, key) for key in any_of):
            raise HTTPException(status_code=403, detail=FEATURE_DISABLED_DETAIL)
        if by_provider:
            key = SOCIAL_FEATURES.get(str(request.path_params.get("provider", "")))
            if key is None:
                raise HTTPException(status_code=404, detail="Unsupported messaging channel")
            ensure_enabled(client, key)
        if not user or not has_permission(user.role, permission):
            raise HTTPException(status_code=403, detail="Your role cannot do this")
        return PortalActor(
            agency_id=client.agency_id,
            id=user.id,
            client_id=client.id,
            name=user.name,
            email=user.email,
            features=frozenset(enabled_keys(client)),
        )

    ACTORS.add(actor)
    return actor


portal_actor = _door(AGENTS_MANAGE, all_of=(FEATURE,))
whatsapp_actor = _door(CHANNELS_MANAGE, all_of=("channels.whatsapp",))
whatsapp_cloud_actor = _door(CHANNELS_MANAGE, all_of=("channels.whatsapp_cloud",))
webchat_actor = _door(CHANNELS_MANAGE, all_of=("channels.webchat",))
social_actor = _door(CHANNELS_MANAGE, by_provider=True)
social_config_actor = _door(CHANNELS_MANAGE, any_of=("channels.instagram", "channels.messenger"))
# The client's name and agents, which every channel screen draws its agent picker from.
channel_client_actor = _door(CHANNELS_MANAGE, any_of=CHANNEL_FEATURES)


def _as_portal(endpoint, actor_dependency: Callable = portal_actor, redact: Callable | None = None):
    """The same endpoint, asking for the portal actor where it asked for the user.

    ``redact`` sees what the endpoint answers (with the actor) before it is
    validated and sent, and returns what the portal may see of it.
    """
    signature = inspect.signature(endpoint)
    swapped = []
    replaced = False
    for parameter in signature.parameters.values():
        default = parameter.default
        if isinstance(default, DependsParam) and default.dependency is get_current_user:
            parameter = parameter.replace(default=Depends(actor_dependency))
            replaced = True
        swapped.append(parameter)
    if not replaced:
        raise RuntimeError(f"{endpoint.__name__} does not take get_current_user; it cannot be mounted in the portal")

    def shown(result, kwargs):
        if redact is None or result is None:
            return result
        actor = next(value for value in kwargs.values() if isinstance(value, PortalActor))
        return redact(result, actor)

    if inspect.iscoroutinefunction(endpoint):
        @functools.wraps(endpoint)
        async def wrapper(*args, **kwargs):
            return shown(await endpoint(*args, **kwargs), kwargs)
    else:
        @functools.wraps(endpoint)
        def wrapper(*args, **kwargs):
            return shown(endpoint(*args, **kwargs), kwargs)
    wrapper.__signature__ = signature.replace(parameters=swapped)
    return wrapper


def _each(result, hide: Callable):
    """Apply ``hide`` to a channel, or to every channel of a list."""
    if isinstance(result, list):
        return [hide(item) for item in result]
    return hide(result) if isinstance(result, dict) else result


def _hide_cloud_details(result, actor):
    """A WhatsApp API number as the portal sees it. The screen draws the status,
    the number, its quality and the webhook address; the provider's own ids and
    the webhook verify token stay with the agency."""
    return _each(result, lambda row: {
        **row,
        "webhook_verify_token": "",
        "provider_profile_id": None,
        "waba_id": None,
        "phone_number_id": "",
        "external_account_id": "",
    })


def _hide_social_details(result, actor):
    """An Instagram or Messenger account as the portal sees it: which app
    authorized it and with which scopes is the agency's business."""
    return _each(result, lambda row: {**row, "app_id": None, "granted_scopes": []} if "app_id" in row else row)


def _social_config(result, actor):
    """Whether authorization is available, for the types on for this client only."""
    wanted = {name for name, key in SOCIAL_FEATURES.items() if key in actor.features}
    return {name: {**value, "webhook_url": ""} for name, value in result.items() if name in wanted}


class Mount(NamedTuple):
    """One group of panel routes: the source router, the ``(METHOD, panel path)``
    pairs to mount (None for all of it), who the routes act as and what is
    blanked from their answers."""

    source: APIRouter
    only: frozenset[tuple[str, str]] | None
    actor: Callable = portal_actor
    redact: Callable | None = None


MOUNTED: tuple[Mount, ...] = (
    # Agents, their knowledge (documents and Q&A), prompt preview, escalation rules.
    Mount(agents.router, None),
    # Custom tools: HTTP endpoints and MCP servers (with the connection test).
    Mount(agent_tools.router, None),
    # What the escalation editor and the pickers need from the client itself.
    Mount(clients.router, frozenset({
        ("GET", "/clients"),
        ("GET", "/clients/{client_id}/teams"),
        ("GET", "/clients/{client_id}/portal-users"),
        ("GET", "/clients/{client_id}/contact-tags"),
        ("PATCH", "/clients/{client_id}/contact-tags/{tag_id}"),
    })),
    # The playground: rehearsal threads with the agent, answered by the real model.
    Mount(conversations.router, frozenset({
        ("GET", "/conversations"),
        ("POST", "/conversations"),
        ("GET", "/conversations/{conversation_id}"),
        ("DELETE", "/conversations/{conversation_id}"),
        ("POST", "/conversations/{conversation_id}/messages"),
        ("POST", "/conversations/{conversation_id}/media"),
        ("GET", "/conversations/{conversation_id}/attachments/{attachment_id}"),
    })),
    # Model names, context windows and prices: reference data, no secrets.
    Mount(catalog.router, None),
    # Channels. Each type is its own function: a whole type or nothing, never one line.
    # WhatsApp QR lines (the QR itself is meant to be shown; the session stays encrypted).
    Mount(whatsapp.router, frozenset({
        ("GET", "/whatsapp/clients/{client_id}/channels"),
        ("POST", "/whatsapp/clients/{client_id}/channels"),
        ("GET", "/whatsapp/channels/{ref}"),
        ("PUT", "/whatsapp/channels/{ref}"),
        ("DELETE", "/whatsapp/channels/{channel_id}"),
        ("POST", "/whatsapp/channels/{ref}/connect"),
        ("POST", "/whatsapp/channels/{ref}/disconnect"),
    }), whatsapp_actor),
    # WhatsApp API numbers (templates are the portal's own screen, not mounted here).
    Mount(whatsapp_cloud.router, frozenset({
        ("GET", "/whatsapp-cloud/clients/{client_id}/channels"),
        ("POST", "/whatsapp-cloud/clients/{client_id}/channels"),
        ("PUT", "/whatsapp-cloud/channels/{ref}"),
        ("DELETE", "/whatsapp-cloud/channels/{channel_id}"),
        ("POST", "/whatsapp-cloud/channels/{ref}/connect"),
        ("POST", "/whatsapp-cloud/channels/{ref}/refresh"),
        ("POST", "/whatsapp-cloud/channels/{ref}/disconnect"),
    }), whatsapp_cloud_actor, _hide_cloud_details),
    # The web chat widget of the client.
    Mount(webchat.router, frozenset({
        ("GET", "/webchat/channels/{client_id}"),
        ("PUT", "/webchat/channels/{client_id}"),
    }), webchat_actor),
    # Instagram and Messenger accounts, through the provider's hosted authorization.
    Mount(social.router, frozenset({("GET", "/social/config")}), social_config_actor, _social_config),
    Mount(social.router, frozenset({
        ("GET", "/social/{provider}/clients/{client_id}/channels"),
        ("PATCH", "/social/{provider}/channels/{channel_id}"),
        ("POST", "/social/{provider}/channels/{ref}/connect"),
        ("POST", "/social/{provider}/channels/{ref}/disconnect"),
        ("POST", "/social/{provider}/oauth/start"),
        ("GET", "/social/{provider}/channels/{ref}/import-history"),
        ("POST", "/social/{provider}/channels/{ref}/import-history"),
    }), social_actor, _hide_social_details),
    # The client's own name and agents, for the agent picker of every channel screen.
    Mount(clients.router, frozenset({("GET", "/clients/{client_id}")}), channel_client_actor),
)

# Extra functions a mounted route needs on top of its own. Routing a tag to a
# team or person edits a tag, which the agency may have kept switched off.
EXTRA_FEATURES: dict[tuple[str, str], tuple[str, ...]] = {
    ("PATCH", "/clients/{client_id}/contact-tags/{tag_id}"): ("tags",),
}


def _mount() -> None:
    for source, only, actor, redact in MOUNTED:
        for route in source.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                if only is not None and (method, route.path) not in only:
                    continue
                router.add_api_route(
                    route.path,
                    _as_portal(route.endpoint, actor, redact),
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
