"""The agency's own Supabase project, and a schema per client inside it.

Adds ``agency_data_stores`` (the agency's OAuth connection to its Supabase
project) and ``client_agency_schemas`` (one schema and one database role per
client of that agency), and lets an OAuth state belong to either a client's
share link or the agency's connection. Everything is new or loosened, so it is
safe against live data: no existing row changes, no client leaves its current
data mode, and the previous release simply ignores the new tables.

``clients.data_mode`` already holds a free string; the new value ``agency``
needs no change to the column.

Revision ID: 0081_agency_backend
Revises: 0080_lead_field_codes
"""

import sqlalchemy as sa
from alembic import op


revision = "0081_agency_backend"
down_revision = "0080_lead_field_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agency_data_stores",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agency_id", sa.Uuid(), sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("provider", sa.String(20), nullable=False, server_default="supabase"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("project_ref", sa.String(40), nullable=False, server_default=""),
        sa.Column("project_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("region", sa.String(40), nullable=False, server_default=""),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("encrypted_access_token", sa.Text(), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "client_agency_schemas",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agency_id", sa.Uuid(), sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("schema_name", sa.String(63), nullable=False),
        sa.Column("role_name", sa.String(63), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("encrypted_dsn", sa.Text(), nullable=True),
        sa.Column("db_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("schema_version", sa.String(40), nullable=False, server_default=""),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_client_agency_schemas_agency_id", "client_agency_schemas", ["agency_id"])

    op.add_column("data_store_oauth_states", sa.Column("agency_data_store_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_data_store_oauth_states_agency", "data_store_oauth_states", "agency_data_stores",
        ["agency_data_store_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("ix_data_store_oauth_states_agency_data_store_id", "data_store_oauth_states", ["agency_data_store_id"])
    op.alter_column("data_store_oauth_states", "data_store_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    # contract: reviewed. The new tables and the agency's authorizations exist
    # only in this release; the previous one never reads them. Clients that
    # were moved to ``agency`` would be left pointing at a mode it does not know,
    # so they are returned to the central database first, which is possible only
    # while their schema still holds their rows: the downgrade refuses otherwise.
    bind = op.get_bind()
    moved = bind.execute(sa.text("SELECT count(*) FROM clients WHERE data_mode = 'agency'")).scalar()
    if moved:
        raise RuntimeError(
            f"{moved} client(s) keep their data in an agency schema; move them back to the "
            "central database from the panel before downgrading"
        )
    bind.execute(sa.text("DELETE FROM data_store_oauth_states WHERE data_store_id IS NULL"))
    op.alter_column("data_store_oauth_states", "data_store_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_index("ix_data_store_oauth_states_agency_data_store_id", table_name="data_store_oauth_states")
    op.drop_constraint("fk_data_store_oauth_states_agency", "data_store_oauth_states", type_="foreignkey")
    op.drop_column("data_store_oauth_states", "agency_data_store_id")
    op.drop_index("ix_client_agency_schemas_agency_id", table_name="client_agency_schemas")
    op.drop_table("client_agency_schemas")
    op.drop_table("agency_data_stores")
