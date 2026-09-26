"""Client storage connections and the resource library.

Adds client_storage_connections (the client's own R2 bucket, credentials
encrypted) and client_resources (files and links the agent may send).

Revision ID: 0069_client_resources
Revises: 0068_scheduled_messages
"""

import sqlalchemy as sa
from alembic import op


revision = "0069_client_resources"
down_revision = "0068_scheduled_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "client_storage_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agency_id", sa.Uuid(), sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("provider", sa.String(20), nullable=False, server_default="r2"),
        sa.Column("account_ref", sa.String(64), nullable=False, server_default=""),
        sa.Column("bucket", sa.String(63), nullable=False, server_default=""),
        sa.Column("region", sa.String(32), nullable=False, server_default="auto"),
        sa.Column("encrypted_access_key_id", sa.Text(), nullable=True),
        sa.Column("encrypted_secret", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_file_mb", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("quota_mb", sa.Integer(), nullable=False, server_default="1024"),
        sa.Column("connect_token", sa.String(64), nullable=False, unique=True),
        sa.Column("connect_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_client_storage_connections_agency_id", "client_storage_connections", ["agency_id"])

    op.create_table(
        "client_resources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agency_id", sa.Uuid(), sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_key", sa.String(300), nullable=True),
        sa.Column("media_kind", sa.String(10), nullable=True),
        sa.Column("mime", sa.String(120), nullable=True),
        sa.Column("filename", sa.String(255), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("message_template", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("client_id", "name", name="uq_client_resources_client_name"),
    )
    op.create_index("ix_client_resources_agency_id", "client_resources", ["agency_id"])
    op.create_index("ix_client_resources_client_id", "client_resources", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_client_resources_client_id", table_name="client_resources")
    op.drop_index("ix_client_resources_agency_id", table_name="client_resources")
    op.drop_table("client_resources")
    op.drop_index("ix_client_storage_connections_agency_id", table_name="client_storage_connections")
    op.drop_table("client_storage_connections")
