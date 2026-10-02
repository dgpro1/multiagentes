"""A counter on each agency user that signing out raises, so a session can be revoked.

Adds ``users.session_version``, 1 for everyone. Sessions issued before this
release carry no version and count as version 1, so nobody is signed out by the
deploy; the first sign-out raises the counter and ends every session of that
person at once. The previous release ignores the column.

Revision ID: 0082_user_session_version
Revises: 0081_agency_backend
"""

import sqlalchemy as sa
from alembic import op


revision = "0082_user_session_version"
down_revision = "0081_agency_backend"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("session_version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    op.drop_column("users", "session_version")
