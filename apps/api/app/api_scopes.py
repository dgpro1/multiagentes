"""What an API integration may do, by scope key.

Same shape as ``app/portal_permissions.py``: a route names a key and asks
whether the credential holds it, never a role name. A preset is only a way of
resolving the same keys, so a custom set of scopes, a new preset or a future
"read-only plus reports" all touch nothing else.

A person signed in with a cookie holds everything; only an API integration is
limited by this catalogue.
"""

CLIENTS_READ = "clients.read"
CLIENTS_WRITE = "clients.write"
AGENTS_READ = "agents.read"
AGENTS_WRITE = "agents.write"
AGENTS_KNOWLEDGE = "agents.knowledge"
AGENTS_TOOLS = "agents.tools"
CHANNELS_READ = "channels.read"
CHANNELS_MANAGE = "channels.manage"
INBOX_READ = "inbox.read"
INBOX_REPLY = "inbox.reply"
INBOX_MANAGE = "inbox.manage"
CONTACTS_READ = "contacts.read"
CONTACTS_MANAGE = "contacts.manage"
TAGS_READ = "tags.read"
TAGS_MANAGE = "tags.manage"
TEAMS_READ = "teams.read"
TEAMS_MANAGE = "teams.manage"
TEMPLATES_READ = "templates.read"
TEMPLATES_MANAGE = "templates.manage"
CANNED_MANAGE = "canned.manage"
PIPELINE_READ = "pipeline.read"
PIPELINE_MANAGE = "pipeline.manage"
CALENDAR_READ = "calendar.read"
CALENDAR_MANAGE = "calendar.manage"
REPORTS_READ = "reports.read"
PROFESSIONALS_READ = "professionals.read"
PROFESSIONALS_MANAGE = "professionals.manage"
LEAD_FIELDS_READ = "lead_fields.read"
LEAD_FIELDS_MANAGE = "lead_fields.manage"
INTEGRATIONS_MANAGE = "integrations.manage"
WEBHOOKS_MANAGE = "webhooks.manage"

ALL: tuple[str, ...] = (
    CLIENTS_READ,
    CLIENTS_WRITE,
    AGENTS_READ,
    AGENTS_WRITE,
    AGENTS_KNOWLEDGE,
    AGENTS_TOOLS,
    CHANNELS_READ,
    CHANNELS_MANAGE,
    INBOX_READ,
    INBOX_REPLY,
    INBOX_MANAGE,
    CONTACTS_READ,
    CONTACTS_MANAGE,
    TAGS_READ,
    TAGS_MANAGE,
    TEAMS_READ,
    TEAMS_MANAGE,
    TEMPLATES_READ,
    TEMPLATES_MANAGE,
    CANNED_MANAGE,
    PIPELINE_READ,
    PIPELINE_MANAGE,
    CALENDAR_READ,
    CALENDAR_MANAGE,
    REPORTS_READ,
    PROFESSIONALS_READ,
    PROFESSIONALS_MANAGE,
    LEAD_FIELDS_READ,
    LEAD_FIELDS_MANAGE,
    INTEGRATIONS_MANAGE,
    WEBHOOKS_MANAGE,
)

# One line each, shown beside the checkbox in the interface.
DESCRIPTIONS: dict[str, str] = {
    CLIENTS_READ: "See the clients and their settings.",
    CLIENTS_WRITE: "Create, edit and delete clients.",
    AGENTS_READ: "See the agents and their configuration.",
    AGENTS_WRITE: "Create, edit and delete agents.",
    AGENTS_KNOWLEDGE: "Read and change an agent's knowledge base.",
    AGENTS_TOOLS: "Read and change an agent's tools and MCP servers.",
    CHANNELS_READ: "See the channels and whether they are connected.",
    CHANNELS_MANAGE: "Connect, configure and remove channels.",
    INBOX_READ: "Read conversations and their messages.",
    INBOX_REPLY: "Reply as a person, take over from the AI and hand it back.",
    INBOX_MANAGE: "Resolve, assign, tag and archive conversations.",
    CONTACTS_READ: "See the contacts.",
    CONTACTS_MANAGE: "Create, edit, merge, import, export and block contacts.",
    TAGS_READ: "See the contact tags.",
    TAGS_MANAGE: "Create, rename, recolor and delete contact tags.",
    TEAMS_READ: "See the teams and who is in them.",
    TEAMS_MANAGE: "Create, edit and delete teams and their members.",
    TEMPLATES_READ: "See the WhatsApp templates of a number.",
    TEMPLATES_MANAGE: "Create and delete WhatsApp templates.",
    CANNED_MANAGE: "Create, edit and delete saved replies.",
    PIPELINE_READ: "See the pipeline stages and the board.",
    PIPELINE_MANAGE: "Create, edit, reorder and delete pipeline stages.",
    CALENDAR_READ: "See the calendar of a client.",
    CALENDAR_MANAGE: "Add, reconnect and remove calendar members.",
    REPORTS_READ: "See the reports.",
    PROFESSIONALS_READ: "See the professionals of a client and their weekly hours.",
    PROFESSIONALS_MANAGE: "Add, edit and delete professionals and their weekly hours.",
    LEAD_FIELDS_READ: "See the custom fields of the lead card.",
    LEAD_FIELDS_MANAGE: "Create, edit and delete the custom fields of the lead card.",
    INTEGRATIONS_MANAGE: "Create and revoke API credentials.",
    WEBHOOKS_MANAGE: "Create, edit and delete webhook subscriptions.",
}

# Offered as presets in the interface. Resolving a preset yields scope keys, so
# the routes see no difference between a preset and a hand-picked set.
READ_ONLY = frozenset(key for key in ALL if key.endswith(".read"))

PRESETS: dict[str, frozenset[str]] = {
    "read_only": READ_ONLY,
    "operator": READ_ONLY | frozenset(
        {
            INBOX_REPLY,
            INBOX_MANAGE,
            CONTACTS_MANAGE,
            TAGS_MANAGE,
            PIPELINE_MANAGE,
            CALENDAR_MANAGE,
            TEAMS_MANAGE,
            PROFESSIONALS_MANAGE,
            LEAD_FIELDS_MANAGE,
        }
    ),
    "full": frozenset(ALL),
}


def known(scope: str) -> bool:
    return scope in DESCRIPTIONS


def resolve(preset: str = "", scopes: list[str] | None = None) -> list[str]:
    """The scope keys an integration holds, validated and sorted.

    An explicit list wins over the preset, so an integration may be narrower
    than any preset offers. Unknown keys are rejected instead of silently
    dropped: a typo would otherwise hand out less than the caller believes.
    """
    if scopes:
        unknown = sorted({scope for scope in scopes if not known(scope)})
        if unknown:
            raise ValueError(f"Unknown scope: {', '.join(unknown)}")
        return sorted(set(scopes))
    if preset and preset not in PRESETS:
        raise ValueError(f"Unknown preset: {preset}")
    return sorted(PRESETS[preset or "read_only"])
