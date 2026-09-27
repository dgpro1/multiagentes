"""The platform owner can block an agency's access.

``agencies.access_status`` ("active" by default) denies every credential of a
blocked agency at its next request: panel sessions and logins, API tokens,
portal and mobile sessions, OAuth grants and pending invitations. Messaging,
channels, data stores and tenant upgrades are deliberately untouched — see
app/services/access_policy.py for the one enforcement point.

Revision ID: 0076_agency_access
Revises: 0075_platform_accounts
"""

import sqlalchemy as sa
from alembic import op


revision = "0076_agency_access"
down_revision = "0075_platform_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive: every existing agency is born "active", so an upgrade changes
    # nothing about what its people can do.
    op.add_column("agencies", sa.Column("access_status", sa.String(length=20), nullable=False, server_default="active"))
    op.add_column("agencies", sa.Column("access_blocked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agencies", sa.Column("access_block_reason", sa.Text(), nullable=False, server_default=""))
    op.add_column("agencies", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # contract: reviewed. The columns only record the operator's decision; no
    # other data depends on them. Dropping them returns to the previous
    # release, where every agency is always active.
    op.drop_column("agencies", "updated_at")
    op.drop_column("agencies", "access_block_reason")
    op.drop_column("agencies", "access_blocked_at")
    op.drop_column("agencies", "access_status")
