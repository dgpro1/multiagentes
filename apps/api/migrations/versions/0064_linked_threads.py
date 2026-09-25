"""Linked threads: a conversation can be absorbed into another one (a lead merge).

Adds ``conversations.primary_conversation_id``, a self reference. The row it
points at is "the lead"; a row carrying it is a linked thread that keeps its
channel, chat key and messages but is read through the primary. The column is
nullable and nothing is backfilled, so it is safe against live data and the
previous release simply ignores it.

Revision ID: 0064_linked_threads
Revises: 0063_lead_card
"""

import sqlalchemy as sa
from alembic import op


revision = "0064_linked_threads"
down_revision = "0063_lead_card"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("primary_conversation_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_conversations_primary_conversation_id",
        "conversations",
        "conversations",
        ["primary_conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_conversations_primary_conversation_id", "conversations", ["primary_conversation_id"])


def downgrade() -> None:
    # contract: reviewed. The column is new in this release and the previous
    # release never reads it, so dropping it only forgets which conversations
    # were merged: after a downgrade the absorbed threads show up as separate
    # leads again, with the lead data they kept in the audit trail only.
    op.drop_index("ix_conversations_primary_conversation_id", table_name="conversations")
    op.drop_constraint("fk_conversations_primary_conversation_id", "conversations", type_="foreignkey")
    op.drop_column("conversations", "primary_conversation_id")
