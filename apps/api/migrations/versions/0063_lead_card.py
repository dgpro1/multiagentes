"""Lead card: the data behind the inbox side panel of a conversation.

Adds the business's responsible person and currency on the client, a company on
the contact, the responsible member and the custom values on the conversation,
and the ``lead_fields`` table where a client defines its own fields. Every new
column is nullable or has a server default, so it is safe against live data and
the previous release simply ignores it.

Revision ID: 0063_lead_card
Revises: 0062_professionals
"""

import sqlalchemy as sa
from alembic import op


revision = "0063_lead_card"
down_revision = "0062_professionals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # conversations first: a reply locks it before anything else.
    op.add_column("conversations", sa.Column("responsible_id", sa.Uuid(), nullable=True))
    op.add_column("conversations", sa.Column("custom_values", sa.JSON(), nullable=False, server_default="{}"))
    op.create_foreign_key(
        "fk_conversations_responsible_id", "conversations", "portal_users", ["responsible_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_conversations_responsible_id", "conversations", ["responsible_id"])

    op.add_column("clients", sa.Column("owner_name", sa.String(120), nullable=True))
    op.add_column("clients", sa.Column("currency", sa.String(3), nullable=False, server_default="USD"))
    op.add_column("contacts", sa.Column("company", sa.String(160), nullable=True))

    op.create_table(
        "lead_fields",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agency_id", sa.Uuid(), sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(60), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("type", sa.String(12), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id", "key", name="uq_lead_fields_client_key"),
    )
    op.create_index("ix_lead_fields_agency_id", "lead_fields", ["agency_id"])
    op.create_index("ix_lead_fields_client_id", "lead_fields", ["client_id"])


def downgrade() -> None:
    # contract: reviewed. Every column and the table are new in this release
    # and the previous release never reads them, so dropping them on the way
    # back loses only the owner names, currencies, companies, responsibles and
    # custom fields entered since, which must be entered again after upgrading.
    op.drop_index("ix_lead_fields_client_id", table_name="lead_fields")
    op.drop_index("ix_lead_fields_agency_id", table_name="lead_fields")
    op.drop_table("lead_fields")
    op.drop_column("contacts", "company")
    op.drop_column("clients", "currency")
    op.drop_column("clients", "owner_name")
    op.drop_index("ix_conversations_responsible_id", table_name="conversations")
    op.drop_constraint("fk_conversations_responsible_id", "conversations", type_="foreignkey")
    op.drop_column("conversations", "custom_values")
    op.drop_column("conversations", "responsible_id")
