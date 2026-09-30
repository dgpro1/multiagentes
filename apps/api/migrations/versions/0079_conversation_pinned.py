"""Pin a lead to the top of the inbox.

``conversations.pinned_at`` is the moment a person pinned this lead, or empty
when they did not. It is one nullable column, so an installation that upgrades
behaves exactly as it did before: nothing is pinned until somebody pins
something, and the previous release simply ignores the column.

The index is partial because the query that needs it asks the opposite
question most of the time: the inbox reads every lead and only a handful are
pinned. Indexing the empty ones would make a write to a hot table pay for rows
nobody looks up.

Revision ID: 0079_conversation_pinned
Revises: 0078_channel_quotas
"""

import sqlalchemy as sa
from alembic import op


revision = "0079_conversation_pinned"
down_revision = "0078_channel_quotas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_conversations_pinned", "conversations", ["pinned_at"],
        postgresql_where=sa.text("pinned_at IS NOT NULL"),
    )


def downgrade() -> None:
    # contract: reviewed. A pin is a person's own arrangement, not a record
    # anything else reads: the inbox falls back to its previous order and every
    # lead stays in the list, only at the bottom again. Dropping the column
    # therefore loses the pins and nothing else, which is what reverting a
    # release with an unreadable column is for.
    op.drop_index("ix_conversations_pinned", table_name="conversations")
    op.drop_column("conversations", "pinned_at")
