"""Professionals: the people a client's business books time with.

A new table, one row per person, with the weekday hours they work stored as a
JSON object (``mon``..``sun`` to a list of ``["HH:MM", "HH:MM"]`` ranges). It
is agency- and client-scoped like every other client-owned table and goes away
with its client.

Revision ID: 0062_professionals
Revises: 0061_portal_api_integrations
"""

import sqlalchemy as sa
from alembic import op


revision = "0062_professionals"
down_revision = "0061_portal_api_integrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "professionals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agency_id", sa.Uuid(), sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("role", sa.String(120), nullable=False, server_default=""),
        sa.Column("color", sa.String(7), nullable=False, server_default="#2f6df0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("slot_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("weekly_hours", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_professionals_agency_id", "professionals", ["agency_id"])
    op.create_index("ix_professionals_client_id", "professionals", ["client_id"])


def downgrade() -> None:
    # contract: reviewed. The table is new in this release and the previous
    # release never reads it, so dropping it on the way back loses only the
    # professionals entered since, which must be entered again after upgrading.
    op.drop_index("ix_professionals_client_id", table_name="professionals")
    op.drop_index("ix_professionals_agency_id", table_name="professionals")
    op.drop_table("professionals")
