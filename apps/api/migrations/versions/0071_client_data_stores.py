"""Each client's own Supabase project.

Adds client_data_stores (the connected project, the OAuth grant and the
connection string of OpenLivery's own role there, all encrypted) and
data_store_oauth_states (single-use consent trips with their PKCE verifier).

Revision ID: 0071_client_data_stores
Revises: 0070_attachment_storage_key
"""

import sqlalchemy as sa
from alembic import op


revision = "0071_client_data_stores"
down_revision = "0070_attachment_storage_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "client_data_stores",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agency_id", sa.Uuid(), sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("provider", sa.String(20), nullable=False, server_default="supabase"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("project_ref", sa.String(40), nullable=False, server_default=""),
        sa.Column("project_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("region", sa.String(40), nullable=False, server_default=""),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("encrypted_access_token", sa.Text(), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("encrypted_dsn", sa.Text(), nullable=True),
        sa.Column("db_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connect_token", sa.String(64), nullable=False, unique=True),
        sa.Column("connect_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_client_data_stores_agency_id", "client_data_stores", ["agency_id"])
    op.create_table(
        "data_store_oauth_states",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("data_store_id", sa.Uuid(), sa.ForeignKey("client_data_stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connect_token", sa.String(64), nullable=False),
        sa.Column("encrypted_verifier", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_data_store_oauth_states_data_store_id", "data_store_oauth_states", ["data_store_id"])
    op.create_index("ix_data_store_oauth_states_expires_at", "data_store_oauth_states", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_data_store_oauth_states_expires_at", table_name="data_store_oauth_states")
    op.drop_index("ix_data_store_oauth_states_data_store_id", table_name="data_store_oauth_states")
    op.drop_table("data_store_oauth_states")
    op.drop_index("ix_client_data_stores_agency_id", table_name="client_data_stores")
    op.drop_table("client_data_stores")
