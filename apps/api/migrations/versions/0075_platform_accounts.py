"""The platform's own accounts, invitations, audit log and slug history.

Four new control-plane tables behind the SUPERADMIN -> AGENCIES -> CLIENTS
layer (work/superadmin-plan.md, phase 2). Additive only: existing agencies,
users and data are untouched, and the previous release keeps working while
this one rolls out.

Revision ID: 0075_platform_accounts
Revises: 0074_decouple_client_data_fks
"""

import sqlalchemy as sa
from alembic import op


revision = "0075_platform_accounts"
down_revision = "0074_decouple_client_data_fks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_admins",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_platform_admins_email"),
    )
    op.create_index("ix_platform_admins_email", "platform_admins", ["email"])
    op.create_table(
        "agency_admin_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agency_id", sa.Uuid(), sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by", sa.Uuid(), sa.ForeignKey("platform_admins.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agency_admin_invitations_agency_id", "agency_admin_invitations", ["agency_id"])
    op.create_index("ix_agency_admin_invitations_email", "agency_admin_invitations", ["email"])
    op.create_table(
        "platform_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("platform_admins.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("target_agency_id", sa.Uuid(), sa.ForeignKey("agencies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resource_type", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("resource_id", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("request_id", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_platform_audit_events_action", "platform_audit_events", ["action"])
    op.create_index("ix_platform_audit_events_target_agency_id", "platform_audit_events", ["target_agency_id"])
    op.create_index("ix_platform_audit_events_created_at", "platform_audit_events", ["created_at"])
    op.create_table(
        "agency_slug_aliases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=180), nullable=False),
        sa.Column("agency_id", sa.Uuid(), sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_agency_slug_aliases_slug"),
    )
    op.create_index("ix_agency_slug_aliases_agency_id", "agency_slug_aliases", ["agency_id"])


def downgrade() -> None:
    # contract: reviewed. The four tables are new and hold only what the
    # platform layer wrote after this release; dropping them returns the
    # schema to the previous release, which never read them.
    op.drop_table("agency_slug_aliases")
    op.drop_table("platform_audit_events")
    op.drop_table("agency_admin_invitations")
    op.drop_table("platform_admins")
