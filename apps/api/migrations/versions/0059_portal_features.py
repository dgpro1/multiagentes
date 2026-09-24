"""The agency chooses, client by client, which functions exist in the portal.

``clients.portal_features`` holds the switches as a JSON object. The column
carries the full defaults as its server default, so every existing client gets
exactly the set it has today (everything the portal already shows is on, the
screens that do not exist yet are off) and a client created by an older release
during a rolling deploy does too. The application reads the column through
``app.portal_features.normalize``, which fills in any key that is missing, so
adding a key to the catalog later needs no data migration.

The defaults are written out here on purpose: a migration describes the
database as it was when it ran, and must not change when the catalog does.

Revision ID: 0059_portal_features
Revises: 0058_conversation_number
"""

import sqlalchemy as sa
from alembic import op


revision = "0059_portal_features"
down_revision = "0058_conversation_number"
branch_labels = None
depends_on = None


DEFAULTS = (
    '{"inbox": true, "contacts": true, "pipeline": true, "calendar": true, "reports": true, '
    '"teams": true, "tags": true, "templates": true, "canned": true, "agents": false, "api": false, '
    '"channels.whatsapp": false, "channels.whatsapp_cloud": false, "channels.instagram": false, '
    '"channels.messenger": false, "channels.webchat": false}'
)


def upgrade() -> None:
    # Additive: one column with a server default, so existing rows are filled
    # in by the database and the previous release keeps working untouched.
    op.add_column("clients", sa.Column("portal_features", sa.JSON(), nullable=False, server_default=DEFAULTS))


def downgrade() -> None:
    # contract: reviewed. The switches are the agency's own choices about the
    # portal and live nowhere else; dropping them resets every client to
    # today's portal. Re-applying the upgrade restores the defaults.
    op.drop_column("clients", "portal_features")
