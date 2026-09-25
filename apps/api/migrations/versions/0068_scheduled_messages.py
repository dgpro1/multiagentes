"""Scheduled messages table.

Adds scheduled_messages table for deferred message sending.

Revision ID: 0068_scheduled_messages
Revises: 0067_professional_services
"""

import sqlalchemy as sa
from alembic import op


revision = "0068_scheduled_messages"
down_revision = "0067_professional_services"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agency_id", sa.Uuid(), sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("via_conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("portal_user_id", sa.Uuid(), sa.ForeignKey("portal_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sender_type", sa.String(20), nullable=False, server_default="human"),
        sa.Column("sender_name", sa.String(180), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_scheduled_messages_agency_id", "scheduled_messages", ["agency_id"])
    op.create_index("ix_scheduled_messages_client_id", "scheduled_messages", ["client_id"])
    op.create_index("ix_scheduled_messages_conversation_id", "scheduled_messages", ["conversation_id"])
    op.create_index("ix_scheduled_messages_via_conversation_id", "scheduled_messages", ["via_conversation_id"])
    op.create_index("ix_scheduled_messages_portal_user_id", "scheduled_messages", ["portal_user_id"])
    op.create_index("ix_scheduled_messages_scheduled_for", "scheduled_messages", ["scheduled_for"])
    op.create_index("ix_scheduled_messages_status", "scheduled_messages", ["status"])


def downgrade() -> None:
    op.drop_index("ix_scheduled_messages_status", table_name="scheduled_messages")
    op.drop_index("ix_scheduled_messages_scheduled_for", table_name="scheduled_messages")
    op.drop_index("ix_scheduled_messages_portal_user_id", table_name="scheduled_messages")
    op.drop_index("ix_scheduled_messages_via_conversation_id", table_name="scheduled_messages")
    op.drop_index("ix_scheduled_messages_conversation_id", table_name="scheduled_messages")
    op.drop_index("ix_scheduled_messages_client_id", table_name="scheduled_messages")
    op.drop_index("ix_scheduled_messages_agency_id", table_name="scheduled_messages")
    op.drop_table("scheduled_messages")
