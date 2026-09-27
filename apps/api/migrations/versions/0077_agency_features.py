"""The platform owner chooses which modules each agency may use.

``agencies.features`` holds the switches as a JSON object, with the full
defaults as its server default — every module that exists today is on, so an
upgrade changes nothing about what any agency can do. ``agencies.plan``
records which preset the switches started from. The application reads the
column through ``app.agency_features.normalize``, which fills in any missing
key, so adding a key to the catalog later needs no data migration.

The defaults are written out here on purpose: a migration describes the
database as it was when it ran, and must not change when the catalog does.

Revision ID: 0077_agency_features
Revises: 0076_agency_access
"""

import sqlalchemy as sa
from alembic import op


revision = "0077_agency_features"
down_revision = "0076_agency_access"
branch_labels = None
depends_on = None


DEFAULTS = (
    '{"clients": true, "agents": true, "inbox": true, "playground": true, "teams": true, '
    '"templates": true, "canned": true, "channels.whatsapp": true, "channels.whatsapp_cloud": true, '
    '"channels.instagram": true, "channels.messenger": true, "channels.webchat": true, '
    '"pipeline": true, "calendar": true, "appointments": true, "professionals": true, '
    '"services": true, "knowledge": true, "resources": true, "storage": true, "data_store": true, '
    '"reports": true, "integrations": true, "branding": true}'
)


def upgrade() -> None:
    # Additive: two columns with server defaults, so existing agencies are
    # filled in by the database and the previous release keeps working.
    op.add_column("agencies", sa.Column("features", sa.JSON(), nullable=False, server_default=DEFAULTS))
    op.add_column("agencies", sa.Column("plan", sa.String(length=40), nullable=False, server_default=""))


def downgrade() -> None:
    # contract: reviewed. The switches are the platform's own choices about
    # this agency's modules and live nowhere else; dropping them resets every
    # agency to "everything on". Re-applying the upgrade restores the defaults.
    op.drop_column("agencies", "plan")
    op.drop_column("agencies", "features")
