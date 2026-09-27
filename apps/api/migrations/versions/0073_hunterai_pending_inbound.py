"""Webhooks kept while a client's data moves between databases.

Adds hunterai_pending_inbound: inbound events that arrive while a client is
"switching" are stored here and replayed once the move ends.

Revision ID: 0073_hunterai_pending_inbound
Revises: 0072_client_data_mode
"""

import sqlalchemy as sa
from alembic import op


revision = "0073_hunterai_pending_inbound"
down_revision = "0072_client_data_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hunterai_pending_inbound",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("client_id", sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_hunterai_pending_inbound_client_id", "hunterai_pending_inbound", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_hunterai_pending_inbound_client_id", table_name="hunterai_pending_inbound")
    op.drop_table("hunterai_pending_inbound")
