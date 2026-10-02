"""A daily spending cap on the AI of each client.

Adds ``clients.ai_daily_cap_usd`` (null means no cap) and ``clients.ai_paused_on``
(the local day on which the cap switched the AI off; null means it is running).
Both are new and nullable, so no client changes behaviour until a cap is set.
The previous release ignores the columns.

Revision ID: 0083_client_ai_cap
Revises: 0082_user_session_version
"""

import sqlalchemy as sa
from alembic import op


revision = "0083_client_ai_cap"
down_revision = "0082_user_session_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("ai_daily_cap_usd", sa.Numeric(10, 2), nullable=True))
    op.add_column("clients", sa.Column("ai_paused_on", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "ai_paused_on")
    op.drop_column("clients", "ai_daily_cap_usd")
