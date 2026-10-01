"""Which tables hold a client's own data (the data plane) and which stay central.

A client may keep its data plane in its own Supabase project (phase B). The
control plane (agency, users, agents, channels, credentials, billing) always
stays in the central database, because inbound webhooks are routed through it
and agency-wide views read it.

Every table must be classified here; ``tests/test_data_plane.py`` fails on a
new table that is in neither set, so the choice is made on purpose.
"""

from sqlalchemy import Column, ForeignKey, Index, MetaData, Table, UniqueConstraint
from sqlalchemy.sql.visitors import replacement_traverse

DATA_PLANE: frozenset[str] = frozenset({
    "contacts",
    "contact_identities",
    "contact_tags",
    "contact_tag_links",
    "conversations",
    "messages",
    "message_attachments",
    "pipeline_stages",
    "lead_fields",
    "appointments",
    "professionals",
    "professional_services",
    "services",
    "scheduled_messages",
    "client_resources",
})

CONTROL_PLANE: frozenset[str] = frozenset({
    "agencies", "users", "clients", "provider_credentials", "agents", "agent_qa", "agent_tools",
    "knowledge_documents", "knowledge_chunks", "escalation_rules", "teams", "team_members",
    "portal_users", "push_devices", "api_integrations", "api_tokens", "api_idempotency_keys",
    "webhook_subscriptions", "webhook_deliveries", "whatsapp_channels", "whatsapp_cloud_channels",
    "whatsapp_coexistence_events", "widget_channels", "social_channels", "social_oauth_states",
    "social_webhook_events", "social_history_imports", "calendar_members", "calendar_oauth_states",
    "client_storage_connections", "client_data_stores", "data_store_oauth_states",
    "canned_responses", "usage_records", "social_outbox", "hunterai_pending_inbound",
    "platform_admins", "agency_admin_invitations", "platform_audit_events", "agency_slug_aliases",
    "agency_data_stores", "client_agency_schemas", "agency_storage_connections",
})


def tenant_metadata() -> MetaData:
    """The data plane as it is created in a client's database: the same tables,
    columns and indexes, without the foreign keys that point at the control
    plane (those rows live in another database; the ids stay as plain UUIDs)."""
    from .database import Base
    from . import models  # noqa: F401 - registers every table on Base.metadata

    target = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name not in DATA_PLANE:
            continue
        columns = []
        for column in table.columns:
            keys = [
                ForeignKey(fk.target_fullname, ondelete=fk.ondelete)
                for fk in column.foreign_keys
                if fk.target_fullname.split(".")[0] in DATA_PLANE
            ]
            columns.append(Column(
                column.name, column.type, *keys,
                primary_key=column.primary_key,
                nullable=column.nullable,
                server_default=column.server_default.arg if column.server_default is not None else None,
            ))
        copy = Table(table.name, target, *columns)
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                copy.append_constraint(UniqueConstraint(*[c.name for c in constraint.columns], name=constraint.name))
        # Indexes may be on expressions (lower(name)) or partial (WHERE ...):
        # rebuild them against the copied columns.
        rebind = {id(column): copy.c[column.name] for column in table.columns}

        def moved(element):
            return replacement_traverse(element, {}, lambda e: rebind.get(id(e)))

        for index in table.indexes:
            options = {key: moved(value) if hasattr(value, "_copy_internals") else value
                       for key, value in index.dialect_kwargs.items()}
            Index(index.name, *[moved(e) for e in index.expressions], unique=index.unique, **options)
    return target
