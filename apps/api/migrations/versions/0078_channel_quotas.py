"""Channel quotas per agency, allocations per client, and the Evolution endpoint per line.

Three additive columns:

``agencies.channel_quotas`` and ``clients.channel_allocations`` hold the numbers
as JSON; an absent key means unlimited, so an empty object leaves every existing
installation behaving exactly as before and no backfill is needed.

``whatsapp_channels.evolution_endpoint`` records which Evolution API deployment
serves a QR line. It is empty for every existing line, which reads as the single
``EVOLUTION_API_URL`` — the behaviour they have today. When a second deployment
is added later, new lines record theirs and the old ones keep their session,
because a Baileys session cannot move between deployments without re-pairing.

Revision ID: 0078_channel_quotas
Revises: 0077_agency_features
"""

import sqlalchemy as sa
from alembic import op


revision = "0078_channel_quotas"
down_revision = "0077_agency_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agencies", sa.Column("channel_quotas", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("clients", sa.Column("channel_allocations", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("whatsapp_channels", sa.Column("evolution_endpoint", sa.String(length=300), nullable=False, server_default=""))


def downgrade() -> None:
    # contract: reviewed. The quotas are the platform's own decisions and the
    # allocations the agency's; both live only in these columns, so dropping them
    # returns every agency and client to "unlimited", which is what an
    # installation that never set them looks like. The Evolution endpoint is an
    # address, not state: dropping it makes every line fall back to the single
    # configured deployment again, which is where the sessions already are.
    op.drop_column("whatsapp_channels", "evolution_endpoint")
    op.drop_column("clients", "channel_allocations")
    op.drop_column("agencies", "channel_quotas")
