import asyncio
import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone

import csv
import io
import re

from fastapi import APIRouter, Cookie, Depends, Header, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import Interval, and_, case, exists, func, literal, or_, select
from sqlalchemy.orm import Session, aliased, joinedload, object_session, selectinload

from ..config import get_settings
from ..database import get_db, new_session
from ..industries import catalog as industry_catalog
from ..models import Agency, Agent, CannedResponse, Client, Contact, ContactTagLink, Conversation, Message, PortalUser, Team, WhatsAppChannel, WhatsAppCloudChannel, now_utc
from ..portal_features import enabled_keys, ensure_enabled
from ..portal_permissions import APPOINTMENTS_MANAGE, CALENDAR_MANAGE, CANNED_MANAGE, CLIENT_MANAGE, CONTACTS_MANAGE, FIELDS_MANAGE, INBOX_DELETE, PIPELINE_MANAGE, PROFESSIONALS_MANAGE, REPORTS_VIEW, SERVICES_MANAGE, TAGS_MANAGE, TEAMS_MANAGE, TEMPLATES_MANAGE, has_permission, permissions_for
from ..ratelimit import login_rate_limit, public_asset_rate_limit
from ..schemas import (
    ClientDetailsOut,
    ClientDetailsUpdate,
    ContactBlockUpdate,
    BulkResult,
    ConversationArchiveUpdate,
    ConversationSelection,
    AgentSummary,
    CannedResponseCreate,
    CannedResponseOut,
    CannedResponseUpdate,
    ContactCreate,
    ContactImportError,
    ContactImportResult,
    ContactTagCreate,
    ContactTagOut,
    ContactTagUpdate,
    ContactTagsSet,
    ContactMergeRequest,
    ContactOut,
    ContactUpdate,
    ConversationStart,
    PortalChannelOut,
    PortalReport,
    ReportAgentRow,
    ReportChannelRow,
    ReportDay,
    TemplateCreate,
    TemplateOut,
    TemplateSampleOut,
    TemplateSend,
    ConversationDetail,
    ConversationAssignmentUpdate,
    ConversationModeUpdate,
    ConversationStatusUpdate,
    ConversationOut,
    PortalInboxOut,
    PortalInboxSummary,
    PortalLoginRequest,
    PortalMemberOut,
    PortalPublicOut,
    PortalSessionOut,
    PortalAvailabilityUpdate,
    ReactionRequest,
    CreateNoteRequest,
    SendMessageRequest,
    ConversationTeamUpdate,
    ConversationPipelineUpdate,
    PipelineBoardOut,
    PipelineCardOut,
    QuickLeadCreate,
    PipelineStageCreate,
    PipelineStageOut,
    PipelineStageReorder,
    PipelineStageUpdate,
    TeamOut,
    TeamUpsert,
)
from ..security import create_portal_token, decode_portal_token, verify_password
from ..schemas_lead_card import (
    LeadCardOut,
    LeadFieldCreate,
    LeadFieldOut,
    LeadFieldUpdate,
    LeadMergeCandidateOut,
    LeadMergeOut,
    LeadMergeRequest,
    LeadUpdate,
)
from ..schemas_professionals import ProfessionalCreate, ProfessionalOut, ProfessionalUpdate
from ..schemas_services import ServiceCreate, ServiceOut, ServiceUpdate
from ..schemas_appointments import AppointmentCreate, AppointmentOut, AppointmentUpdate, AvailabilityDay, AvailabilityResponse
from ..schemas_scheduled_messages import ScheduledMessageCreate, ScheduledMessageOut, ScheduledMessageUpdate
from ..schemas_calendar import CalendarEventsOut, CalendarMemberCreate, CalendarMemberOut, CalendarMemberUpdate, CalendarOverviewOut
from ..services import calendar as calendar_service
from ..services import pipeline as pipeline_service
from ..services import professionals as professionals_service
from ..services import services_catalog
from ..services import appointments as appointments_service
from ..services import scheduled_messages as scheduled_messages_service
from ..services.client_details import apply_details, clear_logo, store_logo
from ..services import channel_accounts
from ..services import lead_card as lead_card_service
from ..services import lead_group, lead_view
from ..services import lead_merge as lead_merge_service
from ..services import lead_fields as lead_fields_service
from ..services.text_search import folded_like
from ..services.contact_edit import (
    assert_phone_free as _assert_phone_free,
    contact_out as _contact_out,
    contact_stats as _contact_stats,
    contact_view,
    get_contact as _portal_contact,
    set_contact_tags,
    update_contact,
)
from ..services.contacts import display_name, merge_contacts, normalize_phone
from ..services.tags import create_tag, delete_tag, get_tag, list_tags, rename_tag, tag_count, tag_out
from ..services.teams import TEAM_CHANNELS, create_team, delete_team, get_team, list_teams, members_out, team_out, update_team
from ..services.whatsapp_templates import (
    create_template,
    delete_template,
    list_templates,
    open_template_conversation,
    read_sample,
    rendered_text,
    send_components,
    send_template,
    template_account,
    upload_sample,
    validate_template_name,
    window_is_open,
    window_open_until,
)
from ..services.conversation_state import record_activity
from ..services.conversation_state import assign, note_reply, set_archived, set_mode, set_status, set_team
from ..services.routing import route_conversation
from ..services.notifications import notify_assigned
from ..services.attachments import attachment_response, conversation_attachment, logo_response
from ..services.operator_media import store_operator_media_reply
from ..services.whatsapp import deliver_reaction, resolve_quote, send_channel_message, signal_channel_read


# Playground conversations are rehearsals by the agency: stored, but never
# shown to the client's team nor counted in its reports.
PLAYGROUND = "playground"

router = APIRouter(prefix="/portal", tags=["Client portal"])
logger = logging.getLogger(__name__)


def _public_client(db: Session, slug: str) -> Client:
    client = db.scalar(
        select(Client).where(Client.portal_slug == slug, Client.portal_enabled.is_(True), Client.is_active.is_(True))
    )
    if not client:
        raise HTTPException(status_code=404, detail="Portal not found or disabled")
    return client


def _portal_client(
    slug: str,
    portal_access_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Client:
    # The browser portal carries the session in an httpOnly cookie. Native
    # clients cannot rely on cookie persistence across restarts, so the same
    # token is also accepted as a bearer credential.
    token = portal_access_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Sign in to the portal")
    payload = decode_portal_token(token)
    if not payload or payload.get("portal_slug") != slug:
        raise HTTPException(status_code=401, detail="The portal session expired")
    try:
        client_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid portal session") from exc
    client = db.scalar(select(Client).where(Client.id == client_id, Client.portal_slug == slug, Client.portal_enabled.is_(True)))
    if not client:
        raise HTTPException(status_code=401, detail="The portal is no longer available")
    # A named session must stay tied to an active member of this portal.
    # Removing a member cannot turn their token into an anonymous legacy one.
    if payload.get("pu"):
        try:
            user = db.get(PortalUser, uuid.UUID(payload["pu"]))
        except (ValueError, TypeError):
            user = None
        if not user or not user.is_active or user.client_id != client.id:
            raise HTTPException(status_code=401, detail="This account is no longer active")
    return client


def _portal_user(
    portal_access_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> PortalUser | None:
    """The person behind the session, when the token names one.

    Sessions issued before portal users existed carry no person, and those keep
    working for reading. Anything that needs to know who acted should depend on
    this and treat None as "the business itself".
    """
    token = portal_access_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    payload = decode_portal_token(token) if token else None
    raw_user = (payload or {}).get("pu")
    if not raw_user:
        return None
    try:
        user = db.get(PortalUser, uuid.UUID(raw_user))
    except (ValueError, TypeError):
        return None
    if not user or not user.is_active:
        return None
    # A cheap heartbeat: the portal polls every few seconds, so last_seen_at
    # tracks real presence with one write a minute at most.
    now = now_utc()
    if user.last_seen_at is None or (now - user.last_seen_at).total_seconds() > 60:
        user.last_seen_at = now
        db.commit()
    return user


def require_permission(key: str):
    """Route dependency: the person behind the session must hold ``key``.

    Sessions with no person behind them hold nothing, so a legacy token can
    still read but never manage. The check is by permission, not by role;
    see app.portal_permissions.
    """

    def dependency(user: PortalUser | None = Depends(_portal_user)) -> None:
        if not user or not has_permission(user.role, key):
            raise HTTPException(status_code=403, detail="Your role cannot do this")

    return dependency


def require_feature(key: str):
    """Route dependency: the agency must have switched ``key`` on for this client.

    It resolves the client exactly as every other portal route does (through
    ``_portal_client``, which FastAPI evaluates once per request), so an
    unauthenticated call still gets its 401 first. This is the server half of
    the agency's per-client feature switches: hiding a screen in the web app is
    only a courtesy, this is what actually refuses the call. It stacks with
    ``require_permission``: the feature must be on for the client AND the role
    must hold the permission.
    """

    def dependency(client: Client = Depends(_portal_client)) -> None:
        ensure_enabled(client, key)

    return dependency


def _sender_name(
    slug: str,
    user: PortalUser | None = Depends(_portal_user),
    db: Session = Depends(get_db),
) -> str:
    """Who to sign a reply as.

    Now that a portal can have several people, a reply should carry the name of
    the one who wrote it rather than the business's. Two cases fall back to the
    business: a session issued before portal users existed, and a person with no
    name set - their e-mail is a login, and this name is shown to the customer.
    """
    if user and user.name.strip():
        return user.name.strip()
    client = db.scalar(select(Client).where(Client.portal_slug == slug))
    return client.name if client else "Support"


def _last_inbound_at(conversation: Conversation):
    stamps = [m.created_at for m in conversation.messages if m.kind == "message" and m.sender_type == "visitor"]
    return max(stamps) if stamps else None


def _window_fields(conversation: Conversation, last_inbound_at) -> dict:
    if conversation.channel in ("instagram", "messenger"):
        from ..services.social_policy import window_fields
        return window_fields(conversation)
    if conversation.channel != "whatsapp_cloud":
        return {"reply_window_until": None, "reply_window_open": True}
    return {"reply_window_until": window_open_until(last_inbound_at), "reply_window_open": window_is_open(last_inbound_at)}


def _present(conversation: Conversation) -> ConversationDetail:
    assignee = conversation.assignee
    detail = ConversationDetail.model_validate(conversation).model_copy(
        update={
            "assignee_name": (assignee.name.strip() or assignee.email) if assignee else None,
            "team_name": conversation.team.name if conversation.team else None,
            "contact_email": (conversation.contact.email or None) if conversation.contact else None,
            **_window_fields(conversation, _last_inbound_at(conversation)),
        }
    )
    # A lead merged from several conversations: one timeline, every thread.
    return lead_view.with_group(object_session(conversation), conversation, detail)


def _detail(db: Session, client: Client, conversation_id: uuid.UUID, *, act: bool = False) -> Conversation:
    """The conversation of this client with its messages. A thread merged into
    another lead reads as that lead; acting through it (``act``) is refused."""
    lead_id = db.scalar(
        select(Conversation.primary_conversation_id).where(
            Conversation.id == conversation_id, Conversation.client_id == client.id
        )
    )
    if lead_id is not None:
        if act:
            raise HTTPException(status_code=409, detail=lead_group.ACT_ON_THE_LEAD)
        conversation_id = lead_id
    conversation = db.scalar(
        select(Conversation)
        .options(selectinload(Conversation.messages).selectinload(Message.attachments), joinedload(Conversation.agent), joinedload(Conversation.assignee))
        .execution_options(populate_existing=True)
        .where(Conversation.id == conversation_id, Conversation.client_id == client.id)
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.get("/{slug}", response_model=PortalPublicOut)
def public_portal(slug: str, db: Session = Depends(get_db)):
    client = _public_client(db, slug)
    agency = db.get(Agency, client.agency_id)
    return {
        "client_name": client.name,
        "portal_title": client.portal_title or f"{client.name} Inbox",
        "portal_slug": client.portal_slug,
        "agency_name": agency.name,
        "agency_brand_color": agency.brand_color,
        # Login page uses the agency logo; the client's own space (inbox) uses
        # the client logo when set.
        "agency_logo_url": f"/api/portal/{slug}/logo" if agency.logo_data else None,
        "client_logo_url": f"/api/portal/{slug}/client-logo" if client.logo_mime else None,
    }


@router.get("/{slug}/logo", dependencies=[Depends(public_asset_rate_limit)])
def public_logo(slug: str, db: Session = Depends(get_db)):
    client = _public_client(db, slug)
    agency = db.get(Agency, client.agency_id)
    if not agency.logo_data or not agency.logo_mime:
        raise HTTPException(status_code=404, detail="Logo not found")
    return logo_response(agency.logo_data, agency.logo_mime)


@router.get("/{slug}/client-logo", dependencies=[Depends(public_asset_rate_limit)])
def public_client_logo(slug: str, db: Session = Depends(get_db)):
    client = _public_client(db, slug)
    if not client.logo_data or not client.logo_mime:
        raise HTTPException(status_code=404, detail="Logo not found")
    return logo_response(client.logo_data, client.logo_mime)


@router.post("/{slug}/login", response_model=PortalSessionOut, dependencies=[Depends(login_rate_limit)])
def portal_login(slug: str, payload: PortalLoginRequest, response: Response, db: Session = Depends(get_db)):
    client = _public_client(db, slug)
    email = payload.email.lower()
    portal_user = db.scalar(
        select(PortalUser).where(
            PortalUser.client_id == client.id,
            PortalUser.email == email,
            PortalUser.is_active.is_(True),
        )
    )
    if not portal_user or not verify_password(payload.password, portal_user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    settings = get_settings()
    response.set_cookie(
        key="portal_access_token",
        value=create_portal_token(
            str(client.id), client.portal_slug, str(portal_user.id) if portal_user else None
        ),
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=settings.access_token_minutes * 60,
        path="/",
    )
    agency = db.get(Agency, client.agency_id)
    return {
        "client_id": client.id,
        "client_name": client.name,
        "portal_slug": client.portal_slug,
        "agency_name": agency.name,
        "user_id": portal_user.id,
        "user_name": portal_user.name.strip() or portal_user.email,
        "role": portal_user.role,
        "permissions": sorted(permissions_for(portal_user.role)),
        "features": enabled_keys(client),
    }


@router.post("/{slug}/logout", status_code=status.HTTP_204_NO_CONTENT)
def portal_logout(response: Response):
    response.delete_cookie("portal_access_token", path="/")


@router.get("/{slug}/me", response_model=PortalSessionOut)
def portal_me(
    slug: str,
    client: Client = Depends(_portal_client),
    user: PortalUser | None = Depends(_portal_user),
    db: Session = Depends(get_db),
):
    agency = db.get(Agency, client.agency_id)
    return {
        "client_id": client.id,
        "client_name": client.name,
        "portal_slug": client.portal_slug,
        "agency_name": agency.name,
        "user_id": user.id if user else None,
        "user_name": (user.name.strip() or user.email) if user else None,
        "role": user.role if user else None,
        "permissions": sorted(permissions_for(user.role)) if user else [],
        "features": enabled_keys(client),
    }


@router.get("/{slug}/members", response_model=list[PortalMemberOut])
def portal_members(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    """The people a conversation can be handed to."""
    return members_out(db, client)


@router.get("/{slug}/teams", response_model=list[TeamOut])
def portal_teams(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return [team_out(db, team) for team in list_teams(db, client)]


@router.post("/{slug}/teams", response_model=TeamOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("teams")), Depends(require_permission(TEAMS_MANAGE))])
def portal_create_team(
    slug: str, payload: TeamUpsert, client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    return team_out(db, create_team(db, client, payload))


@router.patch("/{slug}/teams/{team_id}", response_model=TeamOut, dependencies=[Depends(require_feature("teams")), Depends(require_permission(TEAMS_MANAGE))])
def portal_update_team(
    slug: str,
    team_id: uuid.UUID,
    payload: TeamUpsert,
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    return team_out(db, update_team(db, client, team_id, payload))


@router.delete("/{slug}/teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("teams")), Depends(require_permission(TEAMS_MANAGE))])
def portal_delete_team(
    slug: str, team_id: uuid.UUID, client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    delete_team(db, client, team_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _details_out(slug: str, client: Client) -> dict:
    """The client's own details, as the portal's Details screen reads them.

    ``logo_url`` is the portal's public client-logo route plus a cache buster
    that moves whenever the row does, so a new logo shows at once.
    """
    return {
        "name": client.name,
        "industry": client.industry,
        "business_type": client.business_type,
        "business_custom": client.business_custom,
        "timezone": client.timezone,
        "owner_name": client.owner_name,
        "currency": client.currency or "USD",
        "address": client.address,
        "google_maps_url": client.google_maps_url,
        "business_hours": client.business_hours or {},
        "logo_url": f"/api/portal/{slug}/client-logo?v={int(client.updated_at.timestamp())}" if client.logo_mime else None,
    }


_DETAILS_GUARDS = [Depends(require_feature("details")), Depends(require_permission(CLIENT_MANAGE))]


@router.get("/{slug}/client", response_model=ClientDetailsOut, dependencies=_DETAILS_GUARDS)
def portal_client_details(slug: str, client: Client = Depends(_portal_client)):
    return _details_out(slug, client)


@router.get("/{slug}/industries", dependencies=_DETAILS_GUARDS)
def portal_industries():
    """The industry catalog the Details screen's picker draws from; the same
    list, in the same shape, as the agency's ``GET /api/industries``."""
    return industry_catalog()


@router.patch("/{slug}/client", response_model=ClientDetailsOut, dependencies=_DETAILS_GUARDS)
def portal_update_client_details(
    slug: str, payload: ClientDetailsUpdate, client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    apply_details(db, client, payload.model_dump(exclude_unset=True))
    db.refresh(client)
    return _details_out(slug, client)


@router.post("/{slug}/client/logo", response_model=ClientDetailsOut, dependencies=_DETAILS_GUARDS)
async def portal_upload_client_logo(
    slug: str, file: UploadFile = File(...), client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    await store_logo(db, client, file)
    db.refresh(client)
    return _details_out(slug, client)


@router.delete("/{slug}/client/logo", response_model=ClientDetailsOut, dependencies=_DETAILS_GUARDS)
def portal_delete_client_logo(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    clear_logo(db, client)
    db.refresh(client)
    return _details_out(slug, client)


@router.get("/{slug}/professionals", response_model=list[ProfessionalOut], dependencies=[Depends(require_feature("professionals"))])
def portal_professionals(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return professionals_service.list_professionals(db, client)


@router.post(
    "/{slug}/professionals", response_model=ProfessionalOut, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_feature("professionals")), Depends(require_permission(PROFESSIONALS_MANAGE))],
)
def portal_create_professional(
    slug: str, payload: ProfessionalCreate, client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    return professionals_service.create_professional(db, client, payload)


@router.patch(
    "/{slug}/professionals/{professional_id}", response_model=ProfessionalOut,
    dependencies=[Depends(require_feature("professionals")), Depends(require_permission(PROFESSIONALS_MANAGE))],
)
def portal_update_professional(
    slug: str, professional_id: uuid.UUID, payload: ProfessionalUpdate,
    client: Client = Depends(_portal_client), db: Session = Depends(get_db),
):
    return professionals_service.update_professional(db, client, professional_id, payload)


@router.delete(
    "/{slug}/professionals/{professional_id}", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_feature("professionals")), Depends(require_permission(PROFESSIONALS_MANAGE))],
)
def portal_delete_professional(
    slug: str, professional_id: uuid.UUID, client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    professionals_service.delete_professional(db, client, professional_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{slug}/services", response_model=list[ServiceOut], dependencies=[Depends(require_feature("services"))])
def portal_services(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return services_catalog.list_services(db, client)


@router.post(
    "/{slug}/services", response_model=ServiceOut, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_feature("services")), Depends(require_permission(SERVICES_MANAGE))],
)
def portal_create_service(
    slug: str, payload: ServiceCreate, client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    return services_catalog.create_service(db, client, payload)


@router.patch(
    "/{slug}/services/{service_id}", response_model=ServiceOut,
    dependencies=[Depends(require_feature("services")), Depends(require_permission(SERVICES_MANAGE))],
)
def portal_update_service(
    slug: str, service_id: uuid.UUID, payload: ServiceUpdate,
    client: Client = Depends(_portal_client), db: Session = Depends(get_db),
):
    return services_catalog.update_service(db, client, service_id, payload)


@router.delete(
    "/{slug}/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_feature("services")), Depends(require_permission(SERVICES_MANAGE))],
)
def portal_delete_service(
    slug: str, service_id: uuid.UUID, client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    services_catalog.delete_service(db, client, service_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{slug}/appointments",
    response_model=list[AppointmentOut],
    dependencies=[Depends(require_feature("appointments"))],
)
def portal_appointments(
    slug: str,
    conversation_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    professional_id: uuid.UUID | None = None,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    return appointments_service.list_appointments(
        db,
        client,
        conversation_id=conversation_id,
        contact_id=contact_id,
        professional_id=professional_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )


@router.post(
    "/{slug}/appointments",
    response_model=AppointmentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_feature("appointments")), Depends(require_permission(APPOINTMENTS_MANAGE))],
)
def portal_create_appointment(
    slug: str,
    payload: AppointmentCreate,
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
    user: PortalUser | None = Depends(_portal_user),
):
    appointment = appointments_service.create_appointment(
        db,
        client,
        payload,
        actor_name=user.name if user else "Operator",
    )
    out = AppointmentOut.model_validate(appointment)
    out.professional_name = appointment.professional.name if appointment.professional else None
    out.service_name = appointment.service.name if appointment.service else None
    out.contact_name = appointment.contact.name if appointment.contact else None
    return out


@router.patch(
    "/{slug}/appointments/{appointment_id}",
    response_model=AppointmentOut,
    dependencies=[Depends(require_feature("appointments")), Depends(require_permission(APPOINTMENTS_MANAGE))],
)
def portal_update_appointment(
    slug: str,
    appointment_id: uuid.UUID,
    payload: AppointmentUpdate,
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
    user: PortalUser | None = Depends(_portal_user),
):
    appointment = appointments_service.update_appointment(
        db,
        client,
        appointment_id,
        payload,
        actor_name=user.name if user else "Operator",
    )
    out = AppointmentOut.model_validate(appointment)
    out.professional_name = appointment.professional.name if appointment.professional else None
    out.service_name = appointment.service.name if appointment.service else None
    out.contact_name = appointment.contact.name if appointment.contact else None
    return out


@router.delete(
    "/{slug}/appointments/{appointment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_feature("appointments")), Depends(require_permission(APPOINTMENTS_MANAGE))],
)
def portal_delete_appointment(
    slug: str,
    appointment_id: uuid.UUID,
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
    user: PortalUser | None = Depends(_portal_user),
):
    appointments_service.delete_appointment(
        db,
        client,
        appointment_id,
        actor_name=user.name if user else "Operator",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{slug}/appointments/availability",
    response_model=AvailabilityResponse,
    dependencies=[Depends(require_feature("appointments"))],
)
def portal_appointments_availability(
    slug: str,
    date_from: date = Query(...),
    date_to: date = Query(...),
    service_id: uuid.UUID | None = None,
    professional_id: uuid.UUID | None = None,
    duration_minutes: int | None = None,
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    days = appointments_service.calculate_availability(
        db,
        client,
        date_from,
        date_to,
        service_id=service_id,
        professional_id=professional_id,
        duration_minutes=duration_minutes,
    )
    return AvailabilityResponse(days=[AvailabilityDay(**d) for d in days])


@router.get("/{slug}/lead-fields", response_model=list[LeadFieldOut], dependencies=[Depends(require_feature("inbox"))])
def portal_lead_fields(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return [lead_fields_service.field_out(row) for row in lead_fields_service.list_fields(db, client)]


@router.post(
    "/{slug}/lead-fields", response_model=LeadFieldOut, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_feature("inbox")), Depends(require_permission(FIELDS_MANAGE))],
)
def portal_create_lead_field(
    slug: str, payload: LeadFieldCreate, client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    return lead_fields_service.field_out(lead_fields_service.create_field(db, client, payload))


@router.patch(
    "/{slug}/lead-fields/{field_id}", response_model=LeadFieldOut,
    dependencies=[Depends(require_feature("inbox")), Depends(require_permission(FIELDS_MANAGE))],
)
def portal_update_lead_field(
    slug: str, field_id: uuid.UUID, payload: LeadFieldUpdate,
    client: Client = Depends(_portal_client), db: Session = Depends(get_db),
):
    return lead_fields_service.field_out(lead_fields_service.update_field(db, client, field_id, payload))


@router.delete(
    "/{slug}/lead-fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_feature("inbox")), Depends(require_permission(FIELDS_MANAGE))],
)
def portal_delete_lead_field(
    slug: str, field_id: uuid.UUID, client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    lead_fields_service.delete_field(db, client, field_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{slug}/calendar", response_model=CalendarOverviewOut, dependencies=[Depends(require_feature("calendar"))])
def portal_calendar(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return calendar_service.overview(db, client)


@router.get("/{slug}/calendar/events", response_model=CalendarEventsOut, dependencies=[Depends(require_feature("calendar"))])
async def portal_calendar_events(
    slug: str, start: datetime = Query(...), end: datetime = Query(...),
    client: Client = Depends(_portal_client), db: Session = Depends(get_db),
):
    return await calendar_service.events(db, client, start, end)


@router.post("/{slug}/calendar/members", response_model=CalendarMemberOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_feature("calendar")), Depends(require_permission(CALENDAR_MANAGE))])
def portal_create_calendar_member(slug: str, payload: CalendarMemberCreate, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return calendar_service.member_out(calendar_service.create_member(db, client, payload))


@router.patch("/{slug}/calendar/members/{member_id}", response_model=CalendarMemberOut,
              dependencies=[Depends(require_feature("calendar")), Depends(require_permission(CALENDAR_MANAGE))])
def portal_update_calendar_member(
    slug: str, member_id: uuid.UUID, payload: CalendarMemberUpdate, client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    return calendar_service.member_out(calendar_service.update_member(db, client, member_id, payload))


@router.post("/{slug}/calendar/members/{member_id}/renew-link", response_model=CalendarMemberOut,
             dependencies=[Depends(require_feature("calendar")), Depends(require_permission(CALENDAR_MANAGE))])
def portal_renew_calendar_link(slug: str, member_id: uuid.UUID, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return calendar_service.member_out(calendar_service.renew_link(db, client, member_id))


@router.post("/{slug}/calendar/members/{member_id}/disconnect", response_model=CalendarMemberOut,
             dependencies=[Depends(require_feature("calendar")), Depends(require_permission(CALENDAR_MANAGE))])
async def portal_disconnect_calendar_member(slug: str, member_id: uuid.UUID, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return calendar_service.member_out(await calendar_service.disconnect(db, client, member_id))


@router.delete("/{slug}/calendar/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_feature("calendar")), Depends(require_permission(CALENDAR_MANAGE))])
async def portal_delete_calendar_member(slug: str, member_id: uuid.UUID, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    await calendar_service.delete_member(db, client, member_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{slug}/pipeline/stages", response_model=list[PipelineStageOut], dependencies=[Depends(require_feature("pipeline"))])
def portal_pipeline_stages(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return [pipeline_service.stage_out(db, stage) for stage in pipeline_service.list_stages(db, client)]


@router.post("/{slug}/pipeline/stages", response_model=PipelineStageOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_feature("pipeline")), Depends(require_permission(PIPELINE_MANAGE))])
def portal_create_pipeline_stage(slug: str, payload: PipelineStageCreate, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return pipeline_service.stage_out(db, pipeline_service.create_stage(db, client, payload))


@router.patch("/{slug}/pipeline/stages/{stage_id}", response_model=PipelineStageOut,
              dependencies=[Depends(require_feature("pipeline")), Depends(require_permission(PIPELINE_MANAGE))])
def portal_update_pipeline_stage(slug: str, stage_id: uuid.UUID, payload: PipelineStageUpdate, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return pipeline_service.stage_out(db, pipeline_service.update_stage(db, client, stage_id, payload))


@router.post("/{slug}/pipeline/stages/reorder", response_model=list[PipelineStageOut],
             dependencies=[Depends(require_feature("pipeline")), Depends(require_permission(PIPELINE_MANAGE))])
def portal_reorder_pipeline_stages(slug: str, payload: PipelineStageReorder, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return [pipeline_service.stage_out(db, stage) for stage in pipeline_service.reorder_stages(db, client, payload.stage_ids)]


@router.delete("/{slug}/pipeline/stages/{stage_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_feature("pipeline")), Depends(require_permission(PIPELINE_MANAGE))])
def portal_delete_pipeline_stage(slug: str, stage_id: uuid.UUID, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    pipeline_service.delete_stage(db, client, stage_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{slug}/pipeline/board", response_model=PipelineBoardOut, dependencies=[Depends(require_feature("pipeline"))])
def portal_pipeline_board(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return pipeline_service.board(db, client)


@router.post("/{slug}/pipeline/leads", response_model=PipelineCardOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("pipeline"))])
def portal_create_quick_lead(
    slug: str, payload: QuickLeadCreate, client: Client = Depends(_portal_client),
    sender_name: str = Depends(_sender_name), db: Session = Depends(get_db),
):
    """A manually created deal ("quick lead"), like creating a contact: free
    for anyone signed in. Nothing is sent anywhere."""
    return pipeline_service.create_quick_lead(db, client, payload, actor=sender_name)


@router.patch("/{slug}/conversations/{conversation_id}/pipeline", response_model=ConversationDetail, dependencies=[Depends(require_feature("pipeline"))])
def portal_set_conversation_pipeline(
    slug: str, conversation_id: uuid.UUID, payload: ConversationPipelineUpdate,
    client: Client = Depends(_portal_client), sender_name: str = Depends(_sender_name), db: Session = Depends(get_db),
):
    conversation = _detail(db, client, conversation_id, act=True)
    pipeline_service.move_conversation(db, client, conversation, payload.pipeline_stage_id, payload.deal_value, actor=sender_name)
    return _present(_detail(db, client, conversation_id))


@router.patch("/{slug}/me", response_model=PortalMemberOut)
def portal_update_availability(
    slug: str,
    payload: PortalAvailabilityUpdate,
    client: Client = Depends(_portal_client),
    user: PortalUser | None = Depends(_portal_user),
    db: Session = Depends(get_db),
):
    if not user or user.client_id != client.id:
        raise HTTPException(status_code=401, detail="Sign in with your own user to change availability")
    user.availability = payload.availability
    db.commit()
    return {
        "id": user.id,
        "name": user.name.strip() or user.email,
        "email": user.email,
        "availability": user.availability,
    }


@router.get("/{slug}/agents", response_model=list[AgentSummary], dependencies=[Depends(require_feature("agents"))])
def portal_agents(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return db.scalars(select(Agent).where(Agent.client_id == client.id, Agent.deleted_at.is_(None)).order_by(Agent.name)).all()


def _conversation_page(
    db: Session,
    client: Client,
    user: PortalUser | None,
    *,
    status: str | None = None,
    mode: str | None = None,
    archived: bool = False,
    channel: str | None = None,
    assignee: str | None = None,
    team: uuid.UUID | None = None,
    search: str | None = None,
    unread: bool = False,
    unanswered: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[ConversationOut], int]:
    """One page of the portal inbox and the total behind the same filters."""
    # Same shape as the agency inbox: the latest message and the unread count
    # are resolved in SQL, so the list never loads message histories, and the
    # filters run server-side so paging stays consistent with what is shown.
    # A lead merged from several conversations reads as one row: the messages
    # of its threads are grouped under the primary.
    group_key = lead_group.group_key()
    ranked = (
        select(
            group_key.label("cid"),
            Message.content.label("content"),
            Message.sender_type.label("sender_type"),
            func.row_number().over(partition_by=group_key, order_by=Message.created_at.desc()).label("rn"),
        )
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.kind == "message")
        .subquery()
    )
    last = select(ranked).where(ranked.c.rn == 1).subquery()
    unread_counts = (
        select(group_key.label("cid"), func.count(Message.id).label("n"))
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.sender_type == "visitor",
            Message.is_historical.is_(False),
            or_(Conversation.operator_read_at.is_(None), Message.created_at > Conversation.operator_read_at),
        )
        .group_by(group_key)
    ).subquery()
    # A lead is answered by a person while its primary or any thread is.
    group_human = lead_group.has_human_thread()
    lead_human = or_(Conversation.mode == "human", group_human)
    # Unread is a call to action for a person: the contact wrote and nobody
    # has looked. While the AI answers there is nothing to act on, and a
    # conversation a colleague holds is theirs to catch up on, so unread only
    # counts what is mine or nobody's.
    concerns_me = or_(Conversation.assignee_id.is_(None), Conversation.assignee_id == (user.id if user else None))
    unread_count = case(
        (and_(lead_human, concerns_me), func.coalesce(unread_counts.c.n, 0)),
        else_=0,
    )

    last_inbound = (
        select(group_key.label("cid"), func.max(Message.created_at).label("at"))
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.kind == "message", Message.sender_type == "visitor")
        .group_by(group_key)
        .subquery()
    )
    query = (
        select(Conversation, last.c.content, unread_count.label("unread_count"), PortalUser.name.label("assignee_name"), PortalUser.email.label("assignee_email"), last_inbound.c.at.label("last_inbound_at"), Team.name.label("team_name"), group_human.label("group_human"))
        .outerjoin(last, last.c.cid == Conversation.id)
        .outerjoin(unread_counts, unread_counts.c.cid == Conversation.id)
        .outerjoin(PortalUser, PortalUser.id == Conversation.assignee_id)
        .outerjoin(last_inbound, last_inbound.c.cid == Conversation.id)
        .outerjoin(Team, Team.id == Conversation.team_id)
        .outerjoin(Contact, Contact.id == Conversation.contact_id)
        .where(
            Conversation.client_id == client.id,
            Conversation.channel != PLAYGROUND,
            Contact.blocked_at.is_(None),
            lead_group.is_lead_row(),
        )
    )
    # The archive is its own inbox: archived conversations show only there.
    query = query.where(Conversation.archived_at.is_not(None) if archived else Conversation.archived_at.is_(None))
    if channel is not None:
        if channel not in TEAM_CHANNELS:
            raise HTTPException(status_code=422, detail="Unknown inbox channel")
        query = query.where(Conversation.channel == channel)
    if team is not None:
        query = query.where(Conversation.team_id == team)
    if assignee == "me" and user:
        query = query.where(Conversation.assignee_id == user.id)
    elif assignee == "none":
        query = query.where(lead_human, Conversation.assignee_id.is_(None))
    # No status filter means everything, so clients that predate statuses keep
    # seeing their whole list.
    if status in ("open", "resolved"):
        query = query.where(Conversation.status == status)
    if mode == "human":
        query = query.where(lead_human)
    elif mode == "ai":
        query = query.where(~lead_human)
    if unread:
        query = query.where(unread_count > 0)
    if unanswered:
        # Open, and the contact spoke last: neither the AI nor a person has
        # answered. Activity lines are not messages, so `last` already skips them.
        query = query.where(
            Conversation.status == "open",
            Conversation.archived_at.is_(None),
            last.c.sender_type == "visitor",
        )
    if search and search.strip():
        query = query.where(
            or_(
                folded_like(Conversation.title, search),
                folded_like(Conversation.contact_name, search),
                folded_like(last.c.content, search),
            )
        )
    # A conversation moves up only when the contact writes. Reading it,
    # replying, assigning or resolving all touch updated_at, and none of them
    # should reshuffle the list under the person working it.
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = db.execute(
        query.order_by(func.coalesce(last_inbound.c.at, Conversation.created_at).desc(), Conversation.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    channel_accounts.annotate(db, [row[0] for row in rows])
    stats = lead_group.group_stats(db, [row[0] for row in rows])
    items = [
        lead_view.list_item(
            ConversationOut.model_validate(conv).model_copy(
                update={
                    "preview": (content or "")[:140].strip(),
                    "unread": int(row_unread_count) > 0,
                    "unread_count": int(row_unread_count),
                    "assignee_name": ((assignee_name or "").strip() or assignee_email) if conv.assignee_id else None,
                    "last_inbound_at": last_inbound_at,
                    "team_name": team_name,
                    **_window_fields(conv, last_inbound_at),
                }
            ),
            conv,
            stats[conv.id],
            group_human=bool(row_group_human),
        )
        for conv, content, row_unread_count, assignee_name, assignee_email, last_inbound_at, team_name, row_group_human in rows
    ]
    return items, total


@router.get("/{slug}/conversations", response_model=list[ConversationOut], dependencies=[Depends(require_feature("inbox"))])
def portal_conversations(
    slug: str,
    status: str | None = None,
    mode: str | None = None,
    archived: bool = False,
    channel: str | None = None,
    assignee: str | None = None,
    team: uuid.UUID | None = None,
    search: str | None = None,
    unread: bool = False,
    unanswered: bool = False,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    response: Response = None,  # type: ignore[assignment]
    client: Client = Depends(_portal_client),
    user: PortalUser | None = Depends(_portal_user),
    db: Session = Depends(get_db),
):
    items, total = _conversation_page(
        db, client, user, status=status, mode=mode, archived=archived, channel=channel, assignee=assignee,
        team=team, search=search, unread=unread, unanswered=unanswered, limit=limit, offset=offset,
    )
    # The total for the same filters travels in a header so the list can say
    # how far it has paged without changing the body shape.
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
    return items


@router.get("/{slug}/inbox", response_model=PortalInboxOut, dependencies=[Depends(require_feature("inbox"))])
def portal_inbox(
    slug: str,
    status: str | None = None,
    mode: str | None = None,
    archived: bool = False,
    channel: str | None = None,
    assignee: str | None = None,
    team: uuid.UUID | None = None,
    search: str | None = None,
    unread: bool = False,
    unanswered: bool = False,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    client: Client = Depends(_portal_client),
    user: PortalUser | None = Depends(_portal_user),
    db: Session = Depends(get_db),
):
    """Everything one refresh of the inbox needs, in one response.

    The portal refreshes every few seconds and used to ask three questions for
    it: the page of conversations, the counters, and the open conversations
    held by the signed-in person (to announce a new assignment). Each paid the
    full cost of a request for one part of the same picture. The separate
    endpoints remain for callers that want one part.
    """
    items, total = _conversation_page(
        db, client, user, status=status, mode=mode, archived=archived, channel=channel, assignee=assignee,
        team=team, search=search, unread=unread, unanswered=unanswered, limit=limit, offset=offset,
    )
    mine: list[dict] = []
    if user:
        rows = db.execute(
            select(Conversation.id, Conversation.title)
            .outerjoin(Contact, Contact.id == Conversation.contact_id)
            .where(
                Conversation.client_id == client.id,
                Conversation.channel != PLAYGROUND,
                Contact.blocked_at.is_(None),
                Conversation.archived_at.is_(None),
                Conversation.status == "open",
                Conversation.assignee_id == user.id,
                lead_group.is_lead_row(),
            )
            .order_by(Conversation.created_at.desc())
            .limit(100)
        ).all()
        mine = [{"id": row.id, "title": row.title} for row in rows]
    return {"items": items, "total": total, "summary": _inbox_summary(db, client, user), "mine": mine}


@router.post("/{slug}/conversations/{conversation_id}/read", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("inbox"))])
async def portal_mark_read(
    slug: str,
    conversation_id: uuid.UUID,
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    conversation = db.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.client_id == client.id)
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # Reading a lead reads every thread merged into it.
    threads = lead_group.group_of(db, conversation)
    now = now_utc()
    for thread in threads:
        thread.operator_read_at = now
    db.commit()
    # Opening the thread is the operator reading it: blue-tick the latest
    # visitor message on WhatsApp too. Best-effort by design, and off the
    # request: the portal opens the thread on this answer, and the channel's
    # API takes longer than everything else the click does put together.
    for thread in threads:
        latest_external = db.scalar(
            select(Message.external_message_id)
            .where(
                Message.conversation_id == thread.id,
                Message.role == "user",
                Message.external_message_id.is_not(None),
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        if latest_external:
            _signal_read_later(thread.id, [latest_external])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Read signals in flight, so the loop keeps a reference until each is done.
_read_signals: set["asyncio.Task[None]"] = set()


def _signal_read_later(conversation_id: uuid.UUID, external_ids: list[str]) -> None:
    """Blue-tick ``external_ids`` on the conversation's channel after the
    response has gone out. Opens its own session, since the request's is
    closed by then; the failure of a read receipt is logged, never surfaced."""

    async def run() -> None:
        db = new_session()
        try:
            conversation = db.get(Conversation, conversation_id)
            if conversation:
                await signal_channel_read(db, conversation, external_ids, typing=False)
        except Exception:  # noqa: BLE001
            logger.warning("read signal for conversation %s failed", conversation_id, exc_info=True)
        finally:
            db.close()

    task = asyncio.get_running_loop().create_task(run())
    _read_signals.add(task)
    task.add_done_callback(_read_signals.discard)


async def flush_read_signals() -> None:
    """Wait for every read signal in flight. For tests."""
    if _read_signals:
        await asyncio.gather(*list(_read_signals), return_exceptions=True)


@router.get("/{slug}/contacts", response_model=list[ContactOut], dependencies=[Depends(require_feature("contacts"))])
def portal_contacts(
    slug: str,
    response: Response,
    search: str | None = None,
    tag: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    """One page of contacts, newest activity first. The total for the same
    search travels in X-Total-Count so the list can page without changing
    the body shape that native clients already read."""
    stats = _contact_stats()
    scope = [Contact.client_id == client.id]
    if search and search.strip():
        scope.append(
            or_(
                folded_like(Contact.name, search),
                folded_like(Contact.phone, search),
                folded_like(Contact.email, search),
            )
        )
    if tag is not None:
        scope.append(Contact.id.in_(select(ContactTagLink.contact_id).where(ContactTagLink.tag_id == tag)))
    query = select(Contact, stats).outerjoin(stats, stats.c.cid == Contact.id).where(*scope).options(selectinload(Contact.tags))
    rows = db.execute(
        query.order_by(func.coalesce(stats.c.last_activity_at, Contact.updated_at).desc()).limit(limit).offset(offset)
    ).all()
    total = db.scalar(select(func.count(Contact.id)).where(*scope)) or 0
    response.headers["X-Total-Count"] = str(total)
    return [_contact_out(row[0], row) for row in rows]


@router.post("/{slug}/contacts", response_model=ContactOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("contacts"))])
def portal_create_contact(
    slug: str, payload: ContactCreate, client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    phone = normalize_phone(payload.phone)
    if not phone:
        raise HTTPException(status_code=422, detail="Enter a phone number with its country code")
    _assert_phone_free(db, client, phone)
    contact = Contact(
        client_id=client.id,
        name=payload.name.strip(),
        phone=phone,
        email=(payload.email or None),
        notes=payload.notes.strip(),
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return _contact_out(contact, None)


# --- CSV import and export -------------------------------------------------
# These live above the /{contact_id} routes on purpose: "export", "import" and
# "import-template" would otherwise be parsed as a contact id.

_IMPORT_COLUMNS = {
    "name": {"name", "nombre", "contact", "contacto", "full name", "nombre completo"},
    "phone": {"phone", "telefono", "teléfono", "celular", "mobile", "whatsapp", "number", "numero", "número"},
    "email": {"email", "e-mail", "correo", "mail", "correo electronico", "correo electrónico"},
    "notes": {"notes", "notas", "note", "nota", "comments", "comentarios"},
}
_IMPORT_MAX_ROWS = 5000
_IMPORT_MAX_BYTES = 2 * 1024 * 1024
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Phones are stored as digits only with the country code (573001234567). The
# sample shows that form; the import also tolerates a leading plus and the
# separators spreadsheets add, since they do not change the number.
_PHONE_CHARS_RE = re.compile(r"^\+?[0-9][0-9 ().-]*$")
_TEMPLATE_ROWS = [
    ("name", "phone", "email", "notes"),
    ("Ana Gómez", "573001234567", "ana@example.com", "Prefers mornings"),
    ("Luis Pérez", "525512345678", "", "Asked about pricing"),
]


def _csv_response(rows, filename: str) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    for row in rows:
        writer.writerow(row)
    # The BOM lets Excel open UTF-8 accents correctly without an import wizard.
    body = ("\ufeff" + buffer.getvalue()).encode("utf-8")
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _map_columns(header: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, raw in enumerate(header):
        key = (raw or "").strip().strip("\ufeff").lower()
        for field, aliases in _IMPORT_COLUMNS.items():
            if key in aliases and field not in mapping:
                mapping[field] = index
    return mapping


def _decode_csv(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


@router.get("/{slug}/contacts/import-template", dependencies=[Depends(require_feature("contacts")), Depends(require_permission(CONTACTS_MANAGE))])
def portal_contacts_import_template(slug: str, client: Client = Depends(_portal_client)):
    """A small CSV showing the expected columns, with two example rows."""
    return _csv_response(_TEMPLATE_ROWS, "contacts-template.csv")


@router.get("/{slug}/contacts/export", dependencies=[Depends(require_feature("contacts")), Depends(require_permission(CONTACTS_MANAGE))])
def portal_contacts_export(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    """Every contact of the client as CSV, in the same columns the import reads
    plus the read-only ones (blocked, conversations, created)."""
    stats = _contact_stats()
    rows = db.execute(
        select(Contact, stats).outerjoin(stats, stats.c.cid == Contact.id)
        .where(Contact.client_id == client.id)
        .options(selectinload(Contact.tags))
        .order_by(Contact.name, Contact.created_at)
    ).all()

    def lines():
        yield ("name", "phone", "email", "notes", "tags", "blocked", "conversations", "created_at")
        for row in rows:
            contact = row[0]
            yield (
                contact.name,
                f"+{contact.phone}" if contact.phone else "",
                contact.email or "",
                contact.notes,
                ", ".join(tag.name for tag in contact.tags),
                "yes" if contact.blocked_at else "no",
                int(row.total or 0),
                contact.created_at.date().isoformat(),
            )

    stamp = now_utc().date().isoformat()
    return _csv_response(lines(), f"contacts-{client.portal_slug or 'export'}-{stamp}.csv")


@router.post("/{slug}/contacts/import", dependencies=[Depends(require_feature("contacts")), Depends(require_permission(CONTACTS_MANAGE))], response_model=ContactImportResult)
async def portal_contacts_import(
    slug: str,
    file: UploadFile = File(...),
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    """Load contacts from a CSV. Each row is validated on its own: valid rows
    are saved, invalid ones are reported with their line number and reason,
    so one bad line never blocks the rest. An existing phone is not
    duplicated; the import only fills in fields the contact has empty."""
    data = await file.read()
    if len(data) > _IMPORT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="The file is too large; split it into files under 2 MB")
    text = _decode_csv(data)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        header = next(reader)
    except StopIteration:
        raise HTTPException(status_code=422, detail="The file is empty")
    columns = _map_columns(header)
    if "phone" not in columns:
        raise HTTPException(status_code=422, detail="The file needs a phone column (name, phone, email, notes)")

    result = ContactImportResult()
    seen_in_file: dict[str, int] = {}
    existing = {
        contact.phone: contact
        for contact in db.scalars(select(Contact).where(Contact.client_id == client.id, Contact.phone.is_not(None)))
    }

    def cell(row: list[str], field: str) -> str:
        index = columns.get(field)
        if index is None or index >= len(row):
            return ""
        return (row[index] or "").strip()

    def reject(line: int, row: list[str], reason: str) -> None:
        result.errors.append(ContactImportError(row=line, name=cell(row, "name")[:80], phone=cell(row, "phone")[:40], reason=reason))

    for offset, row in enumerate(reader):
        line = offset + 2  # 1-based, after the header
        if not any((value or "").strip() for value in row):
            continue
        if offset >= _IMPORT_MAX_ROWS:
            result.truncated += 1
            continue
        raw_phone = cell(row, "phone")
        if not raw_phone:
            reject(line, row, "phone_missing")
            continue
        phone = normalize_phone(raw_phone) if _PHONE_CHARS_RE.match(raw_phone) else None
        if not phone or len(phone) > 15:
            reject(line, row, "phone_invalid")
            continue
        name = cell(row, "name")
        email = cell(row, "email") or None
        notes = cell(row, "notes")
        if len(name) > 180:
            reject(line, row, "name_too_long")
            continue
        if email and (len(email) > 255 or not _EMAIL_RE.match(email)):
            reject(line, row, "email_invalid")
            continue
        if len(notes) > 5000:
            reject(line, row, "notes_too_long")
            continue
        if phone in seen_in_file:
            reject(line, row, "duplicate_in_file")
            continue
        seen_in_file[phone] = line

        contact = existing.get(phone)
        if contact is None:
            contact = Contact(client_id=client.id, name=name, phone=phone, email=email, notes=notes)
            db.add(contact)
            existing[phone] = contact
            result.created += 1
            continue
        changed = False
        if name and not contact.name.strip():
            contact.name = name
            changed = True
        if email and not contact.email:
            contact.email = email
            changed = True
        if notes and not contact.notes.strip():
            contact.notes = notes
            changed = True
        if changed:
            result.updated += 1
        else:
            result.unchanged += 1

    db.commit()
    return result


# --- Tags: a catalog the client keeps by hand -------------------------------

@router.get("/{slug}/tags", response_model=list[ContactTagOut])
def portal_tags(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return list_tags(db, client)


@router.post("/{slug}/tags", dependencies=[Depends(require_feature("tags")), Depends(require_permission(TAGS_MANAGE))], response_model=ContactTagOut, status_code=status.HTTP_201_CREATED)
def portal_create_tag(slug: str, payload: ContactTagCreate, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return tag_out(create_tag(db, client, payload.name, payload.color))


@router.patch("/{slug}/tags/{tag_id}", dependencies=[Depends(require_feature("tags")), Depends(require_permission(TAGS_MANAGE))], response_model=ContactTagOut)
def portal_update_tag(
    slug: str, tag_id: uuid.UUID, payload: ContactTagUpdate, client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    tag = get_tag(db, client, tag_id)
    rename_tag(db, client, tag, payload.name, payload.color)
    # Routing is the agency's call (set from the agent editor); the portal
    # can rename and recolor but a route_team_id here is ignored on purpose.
    db.commit()
    db.refresh(tag)
    return tag_out(tag, tag_count(db, tag))


@router.delete("/{slug}/tags/{tag_id}", dependencies=[Depends(require_feature("tags")), Depends(require_permission(TAGS_MANAGE))], status_code=status.HTTP_204_NO_CONTENT)
def portal_delete_tag(slug: str, tag_id: uuid.UUID, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    delete_tag(db, client, tag_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{slug}/contacts/{contact_id}/tags", response_model=ContactOut, dependencies=[Depends(require_feature("tags"))])
def portal_set_contact_tags(
    slug: str, contact_id: uuid.UUID, payload: ContactTagsSet, client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    """Replace the contact's tags with the given set. Unknown ids are ignored
    rather than failing the whole change."""
    return set_contact_tags(db, client, contact_id, payload.tag_ids)


@router.get("/{slug}/contacts/{contact_id}", response_model=ContactOut, dependencies=[Depends(require_feature("contacts"))])
def portal_contact(slug: str, contact_id: uuid.UUID, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return contact_view(db, _portal_contact(db, client, contact_id))


@router.patch("/{slug}/contacts/{contact_id}", response_model=ContactOut, dependencies=[Depends(require_feature("contacts"))])
def portal_update_contact(
    slug: str,
    contact_id: uuid.UUID,
    payload: ContactUpdate,
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    return update_contact(db, client, contact_id, payload)


@router.post("/{slug}/contacts/{contact_id}/merge", dependencies=[Depends(require_feature("contacts")), Depends(require_permission(CONTACTS_MANAGE))], response_model=ContactOut)
def portal_merge_contact(
    slug: str,
    contact_id: uuid.UUID,
    payload: ContactMergeRequest,
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    """Fold the addressed contact into the primary one; the addressed contact
    is deleted and its conversations move over."""
    merged = _portal_contact(db, client, contact_id)
    if payload.primary_contact_id == merged.id:
        raise HTTPException(status_code=409, detail="Pick a different contact to merge into")
    primary = _portal_contact(db, client, payload.primary_contact_id)
    merge_contacts(db, primary, merged)
    db.commit()
    db.refresh(primary)
    stats = _contact_stats()
    row = db.execute(select(stats).where(stats.c.cid == primary.id)).first()
    return _contact_out(primary, row)


@router.post("/{slug}/contacts/{contact_id}/block", dependencies=[Depends(require_feature("contacts")), Depends(require_permission(CONTACTS_MANAGE))], response_model=ContactOut)
def portal_block_contact(
    slug: str,
    contact_id: uuid.UUID,
    payload: ContactBlockUpdate,
    client: Client = Depends(_portal_client),
    sender_name: str = Depends(_sender_name),
    db: Session = Depends(get_db),
):
    """Block or unblock a contact.

    Blocked, their messages are stored but never reach the agent or a phone,
    and their conversations leave the inboxes. Unblocking does not answer the
    backlog: the open conversation is resolved with a note, and the contact's
    next message opens a fresh one that the agent handles as usual.
    """
    contact = _portal_contact(db, client, contact_id)
    open_ones = select(Conversation).where(Conversation.contact_id == contact.id, Conversation.status == "open")
    if payload.blocked and contact.blocked_at is None:
        contact.blocked_at = now_utc()
        for conversation in db.scalars(open_ones).all():
            record_activity(db, conversation, "blocked", actor=sender_name)
    elif not payload.blocked and contact.blocked_at is not None:
        contact.blocked_at = None
        for conversation in db.scalars(open_ones).all():
            set_status(db, conversation, "resolved", actor=sender_name)
            record_activity(db, conversation, "unblocked", actor=sender_name)
    db.commit()
    db.refresh(contact)
    stats = _contact_stats()
    row = db.execute(select(stats).where(stats.c.cid == contact.id)).first()
    return _contact_out(contact, row)


@router.delete("/{slug}/contacts/{contact_id}", dependencies=[Depends(require_feature("contacts")), Depends(require_permission(CONTACTS_MANAGE))], status_code=status.HTTP_204_NO_CONTENT)
def portal_delete_contact(
    slug: str, contact_id: uuid.UUID, client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    # Everything about the person goes with them: their conversations (and
    # with those, the messages and attachments). The portal asks the person
    # to type the contact's name before it calls this.
    contact = _portal_contact(db, client, contact_id)
    for conversation in db.scalars(select(Conversation).where(Conversation.contact_id == contact.id, Conversation.channel != PLAYGROUND)).all():
        db.delete(conversation)
    db.delete(contact)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{slug}/contacts/{contact_id}/conversations", response_model=list[ConversationOut], dependencies=[Depends(require_feature("contacts"))])
def portal_contact_conversations(
    slug: str,
    contact_id: uuid.UUID,
    response: Response,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    since: date | None = None,
    until: date | None = None,
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    """One page of the contact's past cases, newest first, optionally limited
    to the cases opened between two dates (inclusive); the total travels in
    X-Total-Count so the card can page as the person scrolls."""
    contact = _portal_contact(db, client, contact_id)
    scope = [Conversation.contact_id == contact.id, Conversation.channel != PLAYGROUND, lead_group.is_lead_row()]
    if since:
        scope.append(Conversation.created_at >= datetime.combine(since, time.min, tzinfo=timezone.utc))
    if until:
        scope.append(Conversation.created_at < datetime.combine(until + timedelta(days=1), time.min, tzinfo=timezone.utc))
    response.headers["X-Total-Count"] = str(db.scalar(select(func.count(Conversation.id)).where(*scope)) or 0)
    ranked = (
        select(
            Message.conversation_id.label("cid"),
            Message.content.label("content"),
            func.row_number().over(partition_by=Message.conversation_id, order_by=Message.created_at.desc()).label("rn"),
        )
        .where(Message.kind == "message")
        .subquery()
    )
    last = select(ranked).where(ranked.c.rn == 1).subquery()
    rows = db.execute(
        select(Conversation, last.c.content)
        .outerjoin(last, last.c.cid == Conversation.id)
        .where(*scope)
        .order_by(Conversation.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    channel_accounts.annotate(db, [row[0] for row in rows])
    return [
        ConversationOut.model_validate(conv).model_copy(update={"preview": (content or "")[:140].strip()})
        for conv, content in rows
    ]


WINDOW_CLOSED = "The 24-hour reply window is closed. Send an approved template to reach this person."


def _require_open_window(conversation: Conversation) -> None:
    if conversation.channel in ("instagram", "messenger"):
        from ..services.social_policy import require_reply
        require_reply(conversation, human=True)
    if conversation.channel == "whatsapp_cloud" and not window_is_open(_last_inbound_at(conversation)):
        raise HTTPException(status_code=409, detail=WINDOW_CLOSED)


def _cloud_channel(db: Session, client: Client, channel_id: uuid.UUID | None = None) -> WhatsAppCloudChannel | None:
    """The requested WhatsApp API number of the business, or its first enabled one."""
    query = select(WhatsAppCloudChannel).where(WhatsAppCloudChannel.client_id == client.id)
    if channel_id:
        return db.scalar(query.where(WhatsAppCloudChannel.id == channel_id))
    return db.scalar(query.order_by(WhatsAppCloudChannel.is_enabled.desc(), WhatsAppCloudChannel.created_at).limit(1))


def _qr_channel(db: Session, client: Client, channel_id: uuid.UUID | None = None) -> WhatsAppChannel | None:
    """The requested QR line of the business, or its first enabled one."""
    query = select(WhatsAppChannel).where(WhatsAppChannel.client_id == client.id)
    if channel_id:
        return db.scalar(query.where(WhatsAppChannel.id == channel_id))
    return db.scalar(query.order_by(WhatsAppChannel.is_enabled.desc(), WhatsAppChannel.created_at).limit(1))


@router.get("/{slug}/channels", response_model=list[PortalChannelOut])
def portal_channels(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    """Which WhatsApp lines this business has, so the portal knows how it can
    reach a contact first."""
    from ..models import SocialChannel
    from ..services.social_policy import CAPABILITIES
    out = [{"id": item.id, "channel": item.provider, "status": item.status, "label": item.label,
            "external_account_id": item.external_account_id, "display_name": item.display_name,
            "username": item.username, "capabilities": CAPABILITIES.copy()} for item in db.scalars(
        select(SocialChannel).where(SocialChannel.client_id == client.id, SocialChannel.is_enabled.is_(True))
        .order_by(SocialChannel.created_at))]
    for cloud in db.scalars(select(WhatsAppCloudChannel).where(
            WhatsAppCloudChannel.client_id == client.id, WhatsAppCloudChannel.is_enabled.is_(True)).order_by(WhatsAppCloudChannel.created_at)):
        out.append({
            "id": cloud.id, "channel": "whatsapp_cloud", "status": cloud.status, "label": cloud.label,
            "phone_number": cloud.phone_number, "display_name": cloud.display_name,
            "supports_templates": bool(cloud.external_account_id),
        })
    for qr in db.scalars(select(WhatsAppChannel).where(
            WhatsAppChannel.client_id == client.id, WhatsAppChannel.is_enabled.is_(True)).order_by(WhatsAppChannel.created_at)):
        out.append({"id": qr.id, "channel": "whatsapp", "status": qr.status, "label": qr.label,
                    "phone_number": qr.phone_number, "display_name": qr.display_name})
    return out


@router.get("/{slug}/templates", response_model=list[TemplateOut])
async def portal_templates(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return await list_templates(template_account(db, client))


@router.post("/{slug}/templates", dependencies=[Depends(require_feature("templates")), Depends(require_permission(TEMPLATES_MANAGE))], response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
async def portal_create_template(
    slug: str, payload: TemplateCreate, client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    return await create_template(
        template_account(db, client),
        name=validate_template_name(payload.name),
        language=payload.language.strip(),
        category=payload.category,
        header=payload.header.model_dump() if payload.header else None,
        body=payload.body.strip(),
        footer=payload.footer,
        buttons=[button.model_dump() for button in payload.buttons],
        examples=payload.examples,
    )


@router.post("/{slug}/templates/samples", dependencies=[Depends(require_feature("templates")), Depends(require_permission(TEMPLATES_MANAGE))], response_model=TemplateSampleOut, status_code=status.HTTP_201_CREATED)
async def portal_upload_template_sample(
    slug: str, file: UploadFile = File(...), client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    """Store the sample file a media header is reviewed with; the handle
    goes into the template."""
    account_id = template_account(db, client)
    data, mime, filename = await read_sample(file)
    return {"handle": await upload_sample(account_id, data=data, mime=mime, filename=filename)}


@router.delete("/{slug}/templates/{name}", dependencies=[Depends(require_feature("templates")), Depends(require_permission(TEMPLATES_MANAGE))], status_code=status.HTTP_204_NO_CONTENT)
async def portal_delete_template(
    slug: str,
    name: str,
    hsm_id: str | None = Query(default=None, max_length=64),
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    """Remove a template from the business account. There is no disable
    switch, so deletion is how a template is retired."""
    await delete_template(template_account(db, client), name=validate_template_name(name), hsm_id=hsm_id)


def _canned_response(db: Session, client: Client, canned_id: uuid.UUID) -> CannedResponse:
    canned = db.scalar(
        select(CannedResponse).where(CannedResponse.id == canned_id, CannedResponse.client_id == client.id)
    )
    if not canned:
        raise HTTPException(status_code=404, detail="Saved reply not found")
    return canned


def _require_free_shortcut(db: Session, client: Client, shortcut: str, but: uuid.UUID | None = None) -> None:
    clash = db.scalar(
        select(CannedResponse).where(
            CannedResponse.client_id == client.id, CannedResponse.shortcut == shortcut, CannedResponse.id != but
        )
    )
    if clash:
        raise HTTPException(status_code=409, detail="A saved reply already uses that shortcut")


@router.get("/{slug}/canned-responses", response_model=list[CannedResponseOut])
def portal_canned_responses(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    """The saved replies the composer offers when the operator types a slash."""
    return db.scalars(
        select(CannedResponse).where(CannedResponse.client_id == client.id).order_by(CannedResponse.shortcut)
    ).all()


@router.post("/{slug}/canned-responses", dependencies=[Depends(require_feature("canned")), Depends(require_permission(CANNED_MANAGE))], response_model=CannedResponseOut, status_code=status.HTTP_201_CREATED)
def portal_create_canned_response(
    slug: str, payload: CannedResponseCreate, client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    _require_free_shortcut(db, client, payload.shortcut)
    canned = CannedResponse(client_id=client.id, shortcut=payload.shortcut, content=payload.content.strip())
    db.add(canned)
    db.commit()
    db.refresh(canned)
    return canned


@router.patch("/{slug}/canned-responses/{canned_id}", dependencies=[Depends(require_feature("canned")), Depends(require_permission(CANNED_MANAGE))], response_model=CannedResponseOut)
def portal_update_canned_response(
    slug: str,
    canned_id: uuid.UUID,
    payload: CannedResponseUpdate,
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    canned = _canned_response(db, client, canned_id)
    if payload.shortcut is not None:
        _require_free_shortcut(db, client, payload.shortcut, but=canned.id)
        canned.shortcut = payload.shortcut
    if payload.content is not None:
        canned.content = payload.content.strip()
    db.commit()
    db.refresh(canned)
    return canned


@router.delete("/{slug}/canned-responses/{canned_id}", dependencies=[Depends(require_feature("canned")), Depends(require_permission(CANNED_MANAGE))], status_code=status.HTTP_204_NO_CONTENT)
def portal_delete_canned_response(
    slug: str, canned_id: uuid.UUID, client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    db.delete(_canned_response(db, client, canned_id))
    db.commit()


@router.get("/{slug}/reports", dependencies=[Depends(require_feature("reports")), Depends(require_permission(REPORTS_VIEW))], response_model=PortalReport)
def portal_report(
    slug: str,
    from_: date = Query(alias="from"),
    to: date = Query(),
    tz_offset: int = Query(default=0, ge=-840, le=840),
    channel: str | None = Query(default=None, max_length=40),
    assignee_id: uuid.UUID | None = Query(default=None),
    team_id: uuid.UUID | None = Query(default=None),
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    """Basic activity metrics over a local-day range. ``tz_offset`` is the
    viewer's UTC offset in minutes as JavaScript reports it (positive west),
    so days group the way the operator's clock reads them. ``channel``,
    ``assignee_id`` and ``team_id`` narrow every conversation-based number."""
    if to < from_ or (to - from_).days > 366:
        raise HTTPException(status_code=422, detail="Pick a range of at most a year, oldest day first")
    shift = timedelta(minutes=tz_offset)
    start = datetime.combine(from_, time.min, tzinfo=timezone.utc) + shift
    end = datetime.combine(to, time.min, tzinfo=timezone.utc) + shift + timedelta(days=1)
    conv_filters = [Conversation.client_id == client.id, Conversation.channel != PLAYGROUND]
    # Leads count once (a thread merged into another is not one); the message
    # volume below still counts every thread's messages.
    lead_filters = [*conv_filters, lead_group.is_lead_row()]
    # Imported archives did not start or resolve a case in this inbox.
    conv_filters.append(or_(Conversation.social_channel_id.is_(None), exists(
        select(Message.id).where(Message.conversation_id == Conversation.id,
            Message.kind == "message", Message.is_historical.is_(False)).correlate(Conversation))))
    lead_filters.append(conv_filters[-1])
    if channel:
        conv_filters.append(Conversation.channel == channel)
        lead_filters.append(Conversation.channel == channel)
    if assignee_id:
        conv_filters.append(Conversation.assignee_id == assignee_id)
        lead_filters.append(Conversation.assignee_id == assignee_id)
    if team_id:
        conv_filters.append(Conversation.team_id == team_id)
        lead_filters.append(Conversation.team_id == team_id)

    def local_day(column):
        return func.date(column - literal(shift, Interval))

    started_day = local_day(Conversation.created_at)
    started_rows = db.execute(
        select(started_day, func.count())
        .where(*lead_filters, Conversation.created_at >= start, Conversation.created_at < end)
        .group_by(started_day)
    ).all()
    resolved_day = local_day(Conversation.resolved_at)
    resolved_rows = db.execute(
        select(resolved_day, func.count())
        .where(*lead_filters, Conversation.resolved_at >= start, Conversation.resolved_at < end)
        .group_by(resolved_day)
    ).all()
    handoffs, ai_resolved = db.execute(
        select(
            func.count().filter(Conversation.taken_over_at.is_not(None)),
            func.count().filter(Conversation.status == "resolved", Conversation.taken_over_at.is_(None)),
        ).where(*lead_filters, Conversation.created_at >= start, Conversation.created_at < end)
    ).one()

    human_reply = and_(Message.role == "assistant", Message.sender_type == "human")
    ai_reply = and_(Message.role == "assistant", Message.sender_type != "human")
    message_filters = [
        *conv_filters,
        Message.kind == "message",
        Message.is_historical.is_(False),
        Message.created_at >= start,
        Message.created_at < end,
    ]
    message_day = local_day(Message.created_at)
    message_rows = db.execute(
        select(
            message_day,
            func.count().filter(Message.role == "user"),
            func.count().filter(ai_reply),
            func.count().filter(human_reply),
        )
        .select_from(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(*message_filters)
        .group_by(message_day)
    ).all()

    days = {from_ + timedelta(days=i): ReportDay(date=from_ + timedelta(days=i)) for i in range((to - from_).days + 1)}
    for day, count in started_rows:
        if day in days:
            days[day].started = count
    for day, count in resolved_rows:
        if day in days:
            days[day].resolved = count
    for day, inbound_count, ai_count, human_count in message_rows:
        if day in days:
            days[day].inbound, days[day].ai_replies, days[day].human_replies = inbound_count, ai_count, human_count
    inbound = sum(d.inbound for d in days.values())
    ai_replies = sum(d.ai_replies for d in days.values())
    human_replies = sum(d.human_replies for d in days.values())

    channel_rows = db.execute(
        select(Conversation.channel, func.count())
        .where(*lead_filters, Conversation.created_at >= start, Conversation.created_at < end)
        .group_by(Conversation.channel)
        .order_by(func.count().desc())
    ).all()

    active_contacts = db.scalar(
        select(func.count(func.distinct(Conversation.contact_id))).where(
            *lead_filters,
            Conversation.contact_id.is_not(None),
            Conversation.created_at >= start,
            Conversation.created_at < end,
        )
    )
    open_now = db.scalar(
        select(func.count()).select_from(Conversation).where(*lead_filters, Conversation.status != "resolved")
    )
    avg_first_reply = db.scalar(
        select(func.avg(func.extract("epoch", Conversation.first_reply_at - Conversation.created_at))).where(
            *lead_filters,
            Conversation.first_reply_at.is_not(None),
            Conversation.created_at >= start,
            Conversation.created_at < end,
        )
    )
    avg_resolution = db.scalar(
        select(func.avg(func.extract("epoch", Conversation.resolved_at - Conversation.created_at))).where(
            *lead_filters,
            Conversation.resolved_at >= start,
            Conversation.resolved_at < end,
        )
    )

    users = db.execute(
        select(PortalUser.id, PortalUser.name, PortalUser.availability).where(PortalUser.client_id == client.id)
    ).all()
    replies_by_user = dict(
        db.execute(
            select(Message.portal_user_id, func.count())
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                *conv_filters,
                Message.kind == "message",
                human_reply,
                Message.portal_user_id.is_not(None),
                Message.is_historical.is_(False),
                Message.created_at >= start,
                Message.created_at < end,
            )
            .group_by(Message.portal_user_id)
        ).all()
    )
    assigned_by_user = dict(
        db.execute(
            select(Conversation.assignee_id, func.count())
            .where(
                *lead_filters,
                Conversation.assignee_id.is_not(None),
                Conversation.assigned_at >= start,
                Conversation.assigned_at < end,
            )
            .group_by(Conversation.assignee_id)
        ).all()
    )
    open_by_user = dict(
        db.execute(
            select(Conversation.assignee_id, func.count())
            .where(
                *lead_filters,
                Conversation.assignee_id.is_not(None),
                Conversation.status != "resolved",
            )
            .group_by(Conversation.assignee_id)
        ).all()
    )
    agents = sorted(
        (
            ReportAgentRow(
                name=name or "",
                availability=availability,
                replies=replies_by_user.get(user_id, 0),
                assigned=assigned_by_user.get(user_id, 0),
                open_now=open_by_user.get(user_id, 0),
            )
            for user_id, name, availability in users
            if not assignee_id or user_id == assignee_id
        ),
        key=lambda row: (-row.replies, -row.assigned, row.name),
    )

    return PortalReport(
        started=sum(d.started for d in days.values()),
        resolved=sum(d.resolved for d in days.values()),
        open_now=open_now or 0,
        inbound_messages=inbound,
        human_replies=human_replies,
        ai_replies=ai_replies,
        active_contacts=active_contacts or 0,
        agents_online=sum(1 for _, _, availability in users if availability == "online"),
        handoffs=handoffs,
        ai_resolved=ai_resolved,
        avg_first_reply_seconds=float(avg_first_reply) if avg_first_reply is not None else None,
        avg_resolution_seconds=float(avg_resolution) if avg_resolution is not None else None,
        by_day=list(days.values()),
        by_channel=[ReportChannelRow(channel=channel, started=count) for channel, count in channel_rows],
        by_agent=agents,
    )


async def _send_template_to(db: Session, client: Client, to: str, payload: TemplateSend, channel: WhatsAppCloudChannel | None, conversation_id: uuid.UUID | None = None) -> tuple[str | None, str]:
    """Send the template from ``channel`` and return (external id, text as the person reads it)."""
    if channel is None:
        raise HTTPException(status_code=409, detail="This conversation's WhatsApp API number no longer exists")
    account_id = template_account(db, client, channel)
    approved = next(
        (t for t in await list_templates(account_id)
         if t["name"] == payload.name and t["language"] == payload.language and t["status"] == "APPROVED"),
        None,
    )
    if not approved:
        raise HTTPException(status_code=409, detail="That template is not approved for this language")
    thread_id: str | None = None
    if conversation_id is not None:
        row = db.get(Conversation, conversation_id)
        thread_id = row.provider_conversation_id if row else None
    header_format = (approved.get("header") or {}).get("format") or "NONE"
    needs_sample = header_format in ("IMAGE", "VIDEO", "DOCUMENT") and not (payload.header_value or "").strip()
    if thread_id and not needs_sample:
        components = send_components(
            approved,
            body_values=payload.variables,
            header_value=payload.header_value,
            location=payload.location.model_dump() if payload.location else None,
            button_values=payload.button_values,
        )
        external_id = await send_template(
            account_id, thread_id, name=payload.name, language=payload.language, components=components
        )
    else:
        opened = await open_template_conversation(
            account_id, to, template=approved, name=payload.name, language=payload.language,
            body_values=payload.variables, header_value=payload.header_value,
            location=payload.location.model_dump() if payload.location else None,
            button_values=payload.button_values,
        )
        external_id = opened.get("messageId")
        if conversation_id is not None and opened.get("conversationId"):
            row = db.get(Conversation, conversation_id)
            if row and not row.provider_conversation_id:
                row.provider_conversation_id = str(opened["conversationId"])
                db.flush()
    return external_id, rendered_text(approved, body_values=payload.variables, header_value=payload.header_value)


@router.post("/{slug}/contacts/{contact_id}/conversations", response_model=ConversationDetail, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("contacts"))])
async def portal_start_conversation(
    slug: str,
    contact_id: uuid.UUID,
    payload: ConversationStart,
    client: Client = Depends(_portal_client),
    user: PortalUser | None = Depends(_portal_user),
    sender_name: str = Depends(_sender_name),
    db: Session = Depends(get_db),
):
    """Write to a contact first. On the Cloud API that means an approved
    template; on the QR line any text goes. The new conversation is the
    sender's, so the AI does not answer when the person replies."""
    contact = _portal_contact(db, client, contact_id)
    if not contact.phone:
        raise HTTPException(status_code=409, detail="This contact has no phone number")
    cloud, qr = _cloud_channel(db, client, payload.channel_id), _qr_channel(db, client, payload.channel_id)
    channel_name = payload.channel or ("whatsapp_cloud" if cloud and cloud.is_enabled else "whatsapp" if qr and qr.is_enabled else None)
    if channel_name == "whatsapp_cloud" and cloud and cloud.is_enabled:
        if not payload.template:
            raise HTTPException(status_code=422, detail="Starting a conversation on the WhatsApp API takes an approved template")
        fk_field, channel_row, external_chat_id = "whatsapp_cloud_channel_id", cloud, contact.phone
    elif channel_name == "whatsapp" and qr and qr.is_enabled:
        if not (payload.text or "").strip():
            raise HTTPException(status_code=422, detail="Write the message to send")
        fk_field, channel_row, external_chat_id = "whatsapp_channel_id", qr, f"{contact.phone}@s.whatsapp.net"
    else:
        raise HTTPException(status_code=409, detail="This business has no WhatsApp line to send from")

    fk_column = getattr(Conversation, fk_field)
    existing = db.scalar(
        select(Conversation).where(
            fk_column == channel_row.id,
            Conversation.external_chat_id == external_chat_id,
            or_(Conversation.status != "resolved", Conversation.primary_conversation_id.is_not(None)),
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="This contact already has an open conversation on that line")

    now = now_utc()
    conversation = Conversation(
        agency_id=channel_row.agency_id,
        client_id=client.id,
        agent_id=channel_row.agent_id,
        channel=channel_name,
        external_chat_id=external_chat_id,
        contact_id=contact.id,
        contact_name=contact.name or None,
        title=display_name(contact)[:240],
        mode="human",
        taken_over_at=now,
        assignee_id=user.id if user else None,
        assigned_at=now if user else None,
        **{fk_field: channel_row.id},
    )
    db.add(conversation)
    db.flush()

    if channel_name == "whatsapp_cloud":
        external_message_id, text = await _send_template_to(db, client, contact.phone, payload.template, channel_row, conversation.id)
    else:
        text = payload.text.strip()
        external_message_id = await send_channel_message(db, conversation, text)
    record_activity(db, conversation, "started", actor=sender_name)
    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=text,
            sender_type="human",
            sender_name=sender_name,
            portal_user_id=user.id if user else None,
            external_message_id=external_message_id,
        )
    )
    from ..services.phone_handover import cancel_phone_pause
    cancel_phone_pause(conversation)
    note_reply(conversation)
    conversation.updated_at = now_utc()
    db.commit()
    return _present(_detail(db, client, conversation.id))


@router.post("/{slug}/conversations/{conversation_id}/reply-template", response_model=ConversationDetail, dependencies=[Depends(require_feature("inbox")), Depends(require_feature("templates"))])
async def portal_reply_template(
    slug: str,
    conversation_id: uuid.UUID,
    payload: TemplateSend,
    client: Client = Depends(_portal_client),
    user: PortalUser | None = Depends(_portal_user),
    sender_name: str = Depends(_sender_name),
    db: Session = Depends(get_db),
):
    """Reach a person again after the window closed."""
    lead = _detail(db, client, conversation_id, act=True)
    conversation = lead_group.thread_in_group(db, lead, payload.via_conversation_id)
    if conversation.channel != "whatsapp_cloud" or not conversation.external_chat_id:
        raise HTTPException(status_code=409, detail="Templates only exist on the WhatsApp API line")
    if conversation.phone_pause_until is not None:
        set_mode(db, conversation, "human")
        db.commit()
    external_message_id, text = await _send_template_to(
        db, client, conversation.external_chat_id, payload, _cloud_channel(db, client, conversation.whatsapp_cloud_channel_id), conversation.id
    )
    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=text,
            sender_type="human",
            sender_name=sender_name,
            portal_user_id=user.id if user else None,
            external_message_id=external_message_id,
        )
    )
    from ..services.phone_handover import cancel_phone_pause
    cancel_phone_pause(conversation)
    note_reply(conversation)
    conversation.updated_at = now_utc()
    db.commit()
    return _present(_detail(db, client, conversation_id))


@router.get("/{slug}/conversations/summary", response_model=PortalInboxSummary, dependencies=[Depends(require_feature("inbox"))])
def portal_inbox_summary(
    slug: str,
    client: Client = Depends(_portal_client),
    user: PortalUser | None = Depends(_portal_user),
    db: Session = Depends(get_db),
):
    return _inbox_summary(db, client, user)


def _inbox_summary(db: Session, client: Client, user: PortalUser | None) -> dict:
    """Counts behind the list's switches and chips, computed the same way the
    list is so a badge never promises something the filter does not show."""
    # A lead merged from several conversations counts once, as one lead: its
    # threads' messages are read together.
    thread = aliased(Conversation)
    in_lead = or_(thread.id == Conversation.id, thread.primary_conversation_id == Conversation.id)
    unread_exists = (
        select(Message.id)
        .join(thread, thread.id == Message.conversation_id)
        .where(
            in_lead,
            Message.sender_type == "visitor",
            Message.is_historical.is_(False),
            or_(thread.operator_read_at.is_(None), Message.created_at > thread.operator_read_at),
        )
        .correlate(Conversation)
        .exists()
    )
    # The contact wrote last: the most recent real message (activity lines are
    # not messages) is theirs, so nobody has answered yet.
    last_sender = (
        select(Message.sender_type)
        .join(thread, thread.id == Message.conversation_id)
        .where(in_lead, Message.kind == "message")
        .order_by(Message.created_at.desc())
        .limit(1)
        .correlate(Conversation)
        .scalar_subquery()
    )
    live = Conversation.archived_at.is_(None)
    is_open = and_(Conversation.status == "open", live)
    is_human = or_(Conversation.mode == "human", lead_group.has_human_thread())
    is_mine = Conversation.assignee_id == (user.id if user else None)
    concerns_me = or_(Conversation.assignee_id.is_(None), is_mine)
    row = db.execute(
        select(
            func.count().filter(is_open).label("open"),
            func.count().filter(Conversation.status == "resolved", live).label("resolved"),
            func.count().filter(Conversation.archived_at.is_not(None)).label("archived"),
            func.count().filter(is_open, is_human).label("human"),
            func.count().filter(is_open, ~is_human).label("ai"),
            func.count().filter(is_open, is_human, concerns_me, unread_exists).label("unread"),
            func.count().filter(is_open, last_sender == "visitor").label("unanswered"),
            func.count().filter(is_open, is_mine).label("mine"),
            func.count().filter(is_open, is_human, Conversation.assignee_id.is_(None)).label("unassigned"),
        )
        .select_from(Conversation)
        .outerjoin(Contact, Contact.id == Conversation.contact_id)
        .where(
            Conversation.client_id == client.id,
            Conversation.channel != PLAYGROUND,
            Contact.blocked_at.is_(None),
            lead_group.is_lead_row(),
        )
    ).one()
    return {
        "open": row.open, "resolved": row.resolved, "archived": row.archived, "human": row.human, "ai": row.ai,
        "unread": row.unread, "unanswered": row.unanswered, "mine": row.mine, "unassigned": row.unassigned,
    }


@router.post("/{slug}/conversations/archive-resolved", dependencies=[Depends(require_feature("inbox")), Depends(require_permission(INBOX_DELETE))], response_model=BulkResult)
def portal_archive_resolved(
    slug: str,
    client: Client = Depends(_portal_client),
    sender_name: str = Depends(_sender_name),
    db: Session = Depends(get_db),
):
    """Move every resolved conversation of the portal to the archive.

    Open ones are being handled and stay where they are.
    """
    rows = db.scalars(
        select(Conversation).where(
            Conversation.client_id == client.id,
            Conversation.channel != PLAYGROUND,
            Conversation.status == "resolved",
            Conversation.archived_at.is_(None),
            lead_group.is_lead_row(),
        )
    ).all()
    for conversation in rows:
        set_archived(db, conversation, True, actor=sender_name)
    db.commit()
    return {"count": len(rows)}


@router.post("/{slug}/conversations/delete-archived", dependencies=[Depends(require_feature("inbox")), Depends(require_permission(INBOX_DELETE))], response_model=BulkResult)
def portal_delete_archived(
    slug: str,
    payload: ConversationSelection | None = None,
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    """Delete archived conversations, messages included. Final.

    With ``ids`` only those are deleted (and only the archived ones among
    them); without, the whole archive goes.
    """
    query = select(Conversation).where(
        Conversation.client_id == client.id, Conversation.archived_at.is_not(None), lead_group.is_lead_row()
    )
    if payload and payload.ids is not None:
        query = query.where(Conversation.id.in_(payload.ids))
    rows = db.scalars(query).all()
    for conversation in rows:
        db.delete(conversation)
    db.commit()
    return {"count": len(rows)}


@router.patch("/{slug}/conversations/{conversation_id}/archive", dependencies=[Depends(require_feature("inbox")), Depends(require_permission(INBOX_DELETE))], response_model=ConversationDetail)
def portal_archive(
    slug: str,
    conversation_id: uuid.UUID,
    payload: ConversationArchiveUpdate,
    client: Client = Depends(_portal_client),
    sender_name: str = Depends(_sender_name),
    db: Session = Depends(get_db),
):
    conversation = _detail(db, client, conversation_id, act=True)
    if set_archived(db, conversation, payload.archived, actor=sender_name):
        db.commit()
    return _present(_detail(db, client, conversation_id))


@router.delete("/{slug}/conversations/{conversation_id}", dependencies=[Depends(require_feature("inbox")), Depends(require_permission(INBOX_DELETE))], status_code=status.HTTP_204_NO_CONTENT)
def portal_delete_conversation(
    slug: str, conversation_id: uuid.UUID, client: Client = Depends(_portal_client), db: Session = Depends(get_db)
):
    """Delete one conversation for good. Only from the archive, so nothing
    disappears from an inbox in a single step."""
    conversation = _detail(db, client, conversation_id, act=True)
    if conversation.archived_at is None:
        raise HTTPException(status_code=409, detail="Archive the conversation before deleting it")
    db.delete(conversation)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{slug}/conversations/number/{number}", response_model=ConversationDetail, dependencies=[Depends(require_feature("inbox"))])
def portal_conversation_by_number(number: int, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    """One conversation by its per-client number (the "#12" of a lead)."""
    conversation_id = db.scalar(
        select(Conversation.id).where(Conversation.client_id == client.id, Conversation.number == number)
    )
    if conversation_id is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return _present(_detail(db, client, conversation_id))


@router.get("/{slug}/conversations/{conversation_id}", response_model=ConversationDetail, dependencies=[Depends(require_feature("inbox"))])
def portal_conversation(slug: str, conversation_id: uuid.UUID, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return _present(_detail(db, client, conversation_id))


@router.patch("/{slug}/conversations/{conversation_id}/mode", response_model=ConversationDetail, dependencies=[Depends(require_feature("inbox"))])
def portal_mode(
    slug: str,
    conversation_id: uuid.UUID,
    payload: ConversationModeUpdate,
    client: Client = Depends(_portal_client),
    user: PortalUser | None = Depends(_portal_user),
    sender_name: str = Depends(_sender_name),
    db: Session = Depends(get_db),
):
    conversation = _detail(db, client, conversation_id, act=True)
    changed = set_mode(db, conversation, payload.mode, actor=sender_name, user=user)
    if changed:
        db.commit()
    return _present(_detail(db, client, conversation_id))


@router.patch("/{slug}/conversations/{conversation_id}/team", response_model=ConversationDetail, dependencies=[Depends(require_feature("inbox")), Depends(require_feature("teams"))])
async def portal_set_conversation_team(
    slug: str,
    conversation_id: uuid.UUID,
    payload: ConversationTeamUpdate,
    client: Client = Depends(_portal_client),
    sender_name: str = Depends(_sender_name),
    db: Session = Depends(get_db),
):
    conversation = _detail(db, client, conversation_id, act=True)
    team = get_team(db, client, payload.team_id) if payload.team_id else None
    changed = set_team(db, conversation, team, actor=sender_name)
    routed = None
    if changed and team and conversation.mode == "human" and conversation.assignee_id is None:
        routed = route_conversation(db, conversation, actor=sender_name)
    db.commit()
    if routed:
        await notify_assigned(db, conversation, routed, sender_name)
    return _present(_detail(db, client, conversation_id))


@router.post("/{slug}/conversations/{conversation_id}/assignment", response_model=ConversationDetail, dependencies=[Depends(require_feature("inbox"))])
async def portal_assign(
    slug: str,
    conversation_id: uuid.UUID,
    payload: ConversationAssignmentUpdate,
    client: Client = Depends(_portal_client),
    user: PortalUser | None = Depends(_portal_user),
    sender_name: str = Depends(_sender_name),
    db: Session = Depends(get_db),
):
    conversation = _detail(db, client, conversation_id, act=True)
    if not payload.assignee_id:
        # A conversation is either the AI's or a person's. To let go of it,
        # hand it back to the AI rather than leaving it without an owner.
        raise HTTPException(status_code=422, detail="Choose a person, or return the conversation to the AI")
    assignee = db.scalar(
        select(PortalUser).where(
            PortalUser.id == payload.assignee_id, PortalUser.client_id == client.id, PortalUser.is_active.is_(True)
        )
    )
    if not assignee:
        raise HTTPException(status_code=404, detail="That person is not part of this portal")
    changed = assign(db, conversation, assignee, actor=sender_name, actor_user=user)
    if changed:
        db.commit()
        if assignee and (not user or assignee.id != user.id):
            await notify_assigned(db, conversation, assignee, sender_name)
    return _present(_detail(db, client, conversation_id))


@router.get("/{slug}/conversations/{conversation_id}/lead", response_model=LeadCardOut, dependencies=[Depends(require_feature("inbox"))])
def portal_lead(slug: str, conversation_id: uuid.UUID, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return lead_card_service.lead_card(db, client, lead_card_service.get_lead(db, client, conversation_id))


@router.patch("/{slug}/conversations/{conversation_id}/lead", response_model=LeadCardOut, dependencies=[Depends(require_feature("inbox"))])
def portal_update_lead(
    slug: str, conversation_id: uuid.UUID, payload: LeadUpdate,
    client: Client = Depends(_portal_client), db: Session = Depends(get_db),
):
    """Choose the lead's responsible and fill its custom fields. Free for
    anyone with the inbox, like assigning; it never changes who answers."""
    conversation = lead_card_service.get_lead(db, client, conversation_id, act=True)
    lead_card_service.update_lead(db, client, conversation, payload)
    return lead_card_service.lead_card(db, client, lead_card_service.get_lead(db, client, conversation_id))


@router.get("/{slug}/leads/merge-candidates", response_model=list[LeadMergeCandidateOut], dependencies=[Depends(require_feature("inbox"))])
def portal_lead_merge_candidates(
    slug: str,
    q: str | None = Query(default=None, max_length=120),
    exclude: uuid.UUID | None = None,
    limit: int = Query(default=20, ge=1, le=20),
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    """Leads of this portal that can be merged with ``exclude``: primaries only,
    found by contact name, phone, e-mail or number."""
    return lead_merge_service.merge_candidates(db, client, q, exclude, limit)


@router.post("/{slug}/leads/merge", response_model=LeadMergeOut, dependencies=[Depends(require_feature("inbox")), Depends(require_permission(CONTACTS_MANAGE))])
def portal_merge_leads(
    slug: str,
    payload: LeadMergeRequest,
    client: Client = Depends(_portal_client),
    sender_name: str = Depends(_sender_name),
    db: Session = Depends(get_db),
):
    """Fold the secondary lead into the primary one. Final: the secondary stays
    as a linked thread of the primary and its number becomes an alias."""
    primary, secondary_number = lead_merge_service.merge_leads(
        db, client, payload.primary_conversation_id, payload.secondary_conversation_id, sender_name
    )
    return {
        "primary": lead_card_service.lead_card(db, client, lead_card_service.get_lead(db, client, primary.id)),
        "secondary_number": secondary_number,
    }


@router.patch("/{slug}/conversations/{conversation_id}/status", response_model=ConversationDetail, dependencies=[Depends(require_feature("inbox"))])
def portal_status(
    slug: str,
    conversation_id: uuid.UUID,
    payload: ConversationStatusUpdate,
    client: Client = Depends(_portal_client),
    sender_name: str = Depends(_sender_name),
    db: Session = Depends(get_db),
):
    conversation = _detail(db, client, conversation_id, act=True)
    changed = set_status(db, conversation, payload.status, actor=sender_name)
    if changed:
        db.commit()
    return _present(_detail(db, client, conversation_id))


@router.get("/{slug}/conversations/{conversation_id}/attachments/{attachment_id}", dependencies=[Depends(require_feature("inbox"))])
async def portal_attachment(
    slug: str,
    conversation_id: uuid.UUID,
    attachment_id: uuid.UUID,
    playback_format: str | None = Query(default=None, alias="format", pattern="^m4a$"),
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    conversation = _detail(db, client, conversation_id)
    attachment = conversation_attachment(db, conversation, attachment_id)
    if playback_format is None:
        return attachment_response(attachment)
    if attachment.kind != "audio":
        raise HTTPException(status_code=422, detail="Playback conversion is only available for audio")
    from ..services.audio import to_native_audio

    converted = await to_native_audio(attachment.data)
    if converted is None:
        raise HTTPException(status_code=422, detail="This audio could not be prepared for playback")
    return Response(
        content=converted,
        media_type="audio/mp4",
        headers={
            "Cache-Control": "private, max-age=3600",
            "Vary": "Origin",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'inline; filename="voice-note.m4a"',
        },
    )


@router.post("/{slug}/conversations/{conversation_id}/reply-media", response_model=ConversationDetail, dependencies=[Depends(require_feature("inbox"))])
async def portal_reply_media(
    slug: str,
    conversation_id: uuid.UUID,
    file: UploadFile = File(...),
    caption: str = Form(default=""),
    via_conversation_id: uuid.UUID | None = Form(default=None),
    client: Client = Depends(_portal_client),
    user: PortalUser | None = Depends(_portal_user),
    sender_name: str = Depends(_sender_name),
    db: Session = Depends(get_db),
):
    lead = _detail(db, client, conversation_id, act=True)
    conversation = lead_group.thread_in_group(db, lead, via_conversation_id)
    _require_open_window(conversation)
    await store_operator_media_reply(
        db, conversation, file=file, caption=caption, sender_name=sender_name, portal_user_id=user.id if user else None
    )
    return _present(_detail(db, client, conversation_id))


@router.post("/{slug}/conversations/{conversation_id}/reply", response_model=ConversationDetail, dependencies=[Depends(require_feature("inbox"))])
async def portal_reply(
    slug: str,
    conversation_id: uuid.UUID,
    payload: SendMessageRequest,
    client: Client = Depends(_portal_client),
    user: PortalUser | None = Depends(_portal_user),
    sender_name: str = Depends(_sender_name),
    db: Session = Depends(get_db),
):
    lead = _detail(db, client, conversation_id, act=True)
    conversation = lead_group.thread_in_group(db, lead, payload.via_conversation_id)
    _require_open_window(conversation)
    if conversation.channel in ("instagram", "messenger"):
        from ..services.social_delivery import queue_message
        if payload.quoted_message_id:
            raise HTTPException(status_code=422, detail="Quoted replies are not supported by this channel.")
        message = Message(conversation_id=conversation.id, role="assistant", content=payload.content.strip(),
            sender_type="human", sender_name=sender_name, portal_user_id=user.id if user else None)
        db.add(message)
        queue_message(db, conversation, message)
        conversation.updated_at = now_utc()
        db.commit()
        return _present(_detail(db, client, conversation_id))
    if conversation.phone_pause_until is not None:
        set_mode(db, conversation, "human")
        db.commit()
    quoted_id, quoted_external = resolve_quote(db, conversation, payload.quoted_message_id)
    external_message_id = await send_channel_message(
        db, conversation, payload.content.strip(), quoted_external_id=quoted_external
    )
    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=payload.content.strip(),
            sender_type="human",
            sender_name=sender_name,
            portal_user_id=user.id if user else None,
            external_message_id=external_message_id,
            quoted_message_id=quoted_id,
        )
    )
    from ..services.phone_handover import cancel_phone_pause
    cancel_phone_pause(conversation)
    note_reply(conversation)
    conversation.updated_at = now_utc()
    db.commit()
    return _present(_detail(db, client, conversation_id))


@router.post("/{slug}/conversations/{conversation_id}/notes", response_model=ConversationDetail, dependencies=[Depends(require_feature("inbox"))])
async def portal_add_note(
    slug: str,
    conversation_id: uuid.UUID,
    payload: CreateNoteRequest,
    client: Client = Depends(_portal_client),
    user: PortalUser | None = Depends(_portal_user),
    sender_name: str = Depends(_sender_name),
    db: Session = Depends(get_db),
):
    """Add an internal note to the lead from the portal. Notes are visible only to
    client and agency staff, never sent to visitors, and excluded from AI context and metrics."""
    lead = _detail(db, client, conversation_id, act=True)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="Note content cannot be empty.")
    message = Message(
        conversation_id=lead.id,
        role="assistant",
        kind="note",
        content=content,
        sender_type="human",
        sender_name=sender_name,
        portal_user_id=user.id if user else None,
    )
    db.add(message)
    lead.updated_at = now_utc()
    db.commit()
    return _present(_detail(db, client, conversation_id))


@router.get(
    "/{slug}/conversations/{conversation_id}/scheduled-messages",
    response_model=list[ScheduledMessageOut],
    dependencies=[Depends(require_feature("inbox"))],
)
def portal_list_scheduled_messages(
    slug: str,
    conversation_id: uuid.UUID,
    status: str | None = None,
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    return scheduled_messages_service.list_scheduled_messages(
        db, client, conversation_id, status=status
    )


@router.post(
    "/{slug}/conversations/{conversation_id}/scheduled-messages",
    response_model=ScheduledMessageOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_feature("inbox"))],
)
def portal_create_scheduled_message(
    slug: str,
    conversation_id: uuid.UUID,
    payload: ScheduledMessageCreate,
    client: Client = Depends(_portal_client),
    user: PortalUser | None = Depends(_portal_user),
    sender_name: str = Depends(_sender_name),
    db: Session = Depends(get_db),
):
    return scheduled_messages_service.create_scheduled_message(
        db, client, conversation_id, payload, user=user, sender_name=sender_name
    )


@router.patch(
    "/{slug}/conversations/{conversation_id}/scheduled-messages/{scheduled_id}",
    response_model=ScheduledMessageOut,
    dependencies=[Depends(require_feature("inbox"))],
)
def portal_update_scheduled_message(
    slug: str,
    conversation_id: uuid.UUID,
    scheduled_id: uuid.UUID,
    payload: ScheduledMessageUpdate,
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    return scheduled_messages_service.update_scheduled_message(
        db, client, conversation_id, scheduled_id, payload
    )


@router.delete(
    "/{slug}/conversations/{conversation_id}/scheduled-messages/{scheduled_id}",
    response_model=ScheduledMessageOut,
    dependencies=[Depends(require_feature("inbox"))],
)
def portal_cancel_scheduled_message(
    slug: str,
    conversation_id: uuid.UUID,
    scheduled_id: uuid.UUID,
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    return scheduled_messages_service.cancel_scheduled_message(
        db, client, conversation_id, scheduled_id
    )



@router.post(
    "/{slug}/conversations/{conversation_id}/messages/{message_id}/reaction", response_model=ConversationDetail, dependencies=[Depends(require_feature("inbox"))]
)
async def portal_react(
    slug: str,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    payload: ReactionRequest,
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    lead = _detail(db, client, conversation_id, act=True)
    # The message may live on any thread of the lead; the reaction goes out on that thread.
    target = db.scalar(
        select(Message).where(Message.id == message_id, Message.conversation_id.in_(lead_group.group_ids(db, lead)))
    )
    if not target or (payload.via_conversation_id and target.conversation_id != payload.via_conversation_id):
        raise HTTPException(status_code=404, detail="Message not found")
    if target.role != "user":
        raise HTTPException(status_code=409, detail="Reactions go on the customer's messages")
    emoji = payload.emoji.strip()
    conversation = lead if target.conversation_id == lead.id else db.get(Conversation, target.conversation_id)
    await deliver_reaction(db, conversation, target, emoji)
    target.reaction = emoji or None
    db.commit()
    return _present(_detail(db, client, conversation_id))
