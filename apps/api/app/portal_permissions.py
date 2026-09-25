"""What a portal user may do, by role.

The portal has two roles. ``admin`` can do everything; ``agent`` works the
inbox and nothing else. The API never checks the role name: every guarded
route names a permission key and asks whether the person's role holds it, so
the roles are presets over this catalog. A new feature adds its key here and
decides which presets get it; a future custom role is another way of resolving
the same keys and touches nothing else.

Everything not listed is free for anyone signed in: reading the inbox,
replying, taking a conversation from the AI and handing it back, changing its
status, assigning it, creating contacts and tagging them with existing tags,
and their own availability.
"""

# Delete or archive conversations, alone or in bulk.
INBOX_DELETE = "inbox.delete"
# Import, export, delete, merge and block contacts.
CONTACTS_MANAGE = "contacts.manage"
# Create, rename, recolor and delete tags. Putting an existing tag on a
# contact is free.
TAGS_MANAGE = "tags.manage"
# Create and delete WhatsApp templates. Sending an approved one is free.
TEMPLATES_MANAGE = "templates.manage"
# Create, edit and delete saved replies. Using one is free.
CANNED_MANAGE = "canned.manage"
# Create, edit and delete teams and their membership.
TEAMS_MANAGE = "teams.manage"
# The reports tab.
REPORTS_VIEW = "reports.view"
# Add, rename, recolor, delete and reconnect calendar members. Viewing the
# calendar tab and its events is free.
CALENDAR_MANAGE = "calendar.manage"
# Create, rename, recolor, delete and reorder pipeline stages. Viewing the
# board and dragging a card between existing stages is free.
PIPELINE_MANAGE = "pipeline.manage"
# Everything the agency's agent screens do, for the client's own agents: create,
# edit, delete, knowledge, tools, escalation rules, the playground. It only
# takes effect where the agency switched the ``agents`` portal function on.
AGENTS_MANAGE = "agents.manage"
# Everything the agency's channel screens do for the channel types the agency
# switched on (``channels.*`` portal functions): connect, configure and
# disconnect the client's WhatsApp, WhatsApp API, Instagram, Messenger and web
# chat lines. It only takes effect for a type that is on for the client.
CHANNELS_MANAGE = "channels.manage"
# The agency's API screen for the client's own integrations: create, rename and
# delete them, issue and revoke their tokens, manage their webhooks. Admins
# only, and only where the agency switched the ``api`` portal function on. It
# hands out credentials, so what it may grant is narrower than the agency's
# screen (see app.portal_api_access).
API_MANAGE = "api.manage"
# Edit the client's own details (name, industry, business type, time zone,
# logo). Only takes effect where the agency switched the ``details`` portal
# function on; the agency-only fields (activation, portal settings, domain)
# never go through it.
CLIENT_MANAGE = "client.manage"
# Add, edit and delete the client's professionals and their weekly hours. Only
# takes effect where the agency switched the ``professionals`` portal function
# on. Listing them is free for anyone signed in to such a portal.
PROFESSIONALS_MANAGE = "professionals.manage"
# Add, edit and delete the client's services catalog. Only takes effect where
# the agency switched the ``services`` portal function on. Listing them is free
# for anyone signed in to such a portal.
SERVICES_MANAGE = "services.manage"
# Create, rename, reorder and delete the custom fields of the lead card. Only
# takes effect where the agency switched the ``inbox`` portal function on.
# Listing the fields and filling their values on a lead is free for anyone with
# the inbox.
FIELDS_MANAGE = "fields.manage"

# The portal function each permission belongs to, where it belongs to exactly
# one. Guarded routes state both checks themselves; this is the readable map.
PERMISSION_FEATURES: dict[str, str] = {
    AGENTS_MANAGE: "agents",
    API_MANAGE: "api",
    CLIENT_MANAGE: "details",
    PROFESSIONALS_MANAGE: "professionals",
    SERVICES_MANAGE: "services",
    FIELDS_MANAGE: "inbox",
}

PERMISSIONS: tuple[str, ...] = (
    INBOX_DELETE,
    CONTACTS_MANAGE,
    TAGS_MANAGE,
    TEMPLATES_MANAGE,
    CANNED_MANAGE,
    TEAMS_MANAGE,
    REPORTS_VIEW,
    CALENDAR_MANAGE,
    PIPELINE_MANAGE,
    AGENTS_MANAGE,
    CHANNELS_MANAGE,
    API_MANAGE,
    CLIENT_MANAGE,
    PROFESSIONALS_MANAGE,
    SERVICES_MANAGE,
    FIELDS_MANAGE,
)

ROLES: dict[str, frozenset[str]] = {
    "admin": frozenset(PERMISSIONS),
    "agent": frozenset(),
}
DEFAULT_ROLE = "agent"


def permissions_for(role: str | None) -> frozenset[str]:
    """The keys a role holds. An unknown role holds nothing."""
    return ROLES.get(role or "", frozenset())


def has_permission(role: str | None, key: str) -> bool:
    return key in permissions_for(role)
