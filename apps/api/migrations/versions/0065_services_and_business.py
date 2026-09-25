"""Services catalog and client business location details.

Adds services table for business products and services catalog, and adds
address, google_maps_url, and business_hours columns to clients table.

Revision ID: 0065_services_and_business
Revises: 0064_linked_threads
"""

import sqlalchemy as sa
from alembic import op


revision = "0065_services_and_business"
down_revision = "0064_linked_threads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("address", sa.Text(), nullable=True))
    op.add_column("clients", sa.Column("google_maps_url", sa.String(500), nullable=True))
    op.add_column("clients", sa.Column("business_hours", sa.JSON(), nullable=True, server_default="{}"))

    op.create_table(
        "services",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agency_id", sa.Uuid(), sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("modality", sa.String(32), nullable=False, server_default="presencial"),
        sa.Column("requires_deposit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deposit_amount", sa.Float(), nullable=True),
        sa.Column("requirements", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_services_agency_id", "services", ["agency_id"])
    op.create_index("ix_services_client_id", "services", ["client_id"])


def downgrade() -> None:
    # contract: reviewed. Dropping the table and client business profile columns
    # only loses the services entered since this release and location data.
    op.drop_index("ix_services_client_id", table_name="services")
    op.drop_index("ix_services_agency_id", table_name="services")
    op.drop_table("services")
    op.drop_column("clients", "business_hours")
    op.drop_column("clients", "google_maps_url")
    op.drop_column("clients", "address")
