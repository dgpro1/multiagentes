"""An API integration can be made by a client's portal admin.

``api_integrations.created_by`` is a foreign key to ``users`` and stays the
agency user the integration's tokens act as; for an integration made from a
portal that is the agency's owner, because a portal admin is not a row of
``users``. Three additive columns record who really made it: ``created_via_portal``
(also what the webhook sender reads to re-check destinations at send time),
``created_by_portal_user_id`` (cleared if that person is deleted) and
``created_by_portal_label``, their name and e-mail, which outlives the person.
Existing rows read as made by the agency.

Revision ID: 0061_portal_api_integrations
Revises: 0060_portal_channel_flows
"""

import sqlalchemy as sa
from alembic import op


revision = "0061_portal_api_integrations"
down_revision = "0060_portal_channel_flows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_integrations", sa.Column("created_via_portal", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("api_integrations", sa.Column("created_by_portal_user_id", sa.Uuid(), nullable=True))
    op.add_column("api_integrations", sa.Column("created_by_portal_label", sa.String(length=500), nullable=False, server_default=""))
    op.create_foreign_key(
        "fk_api_integrations_portal_user", "api_integrations", "portal_users",
        ["created_by_portal_user_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    # contract: reviewed. The columns only record who made an integration from a
    # portal; the integrations, their tokens and webhooks stay. Re-applying the
    # upgrade adds the columns back empty (every row reads as made by the agency).
    op.drop_constraint("fk_api_integrations_portal_user", "api_integrations", type_="foreignkey")
    op.drop_column("api_integrations", "created_by_portal_label")
    op.drop_column("api_integrations", "created_by_portal_user_id")
    op.drop_column("api_integrations", "created_via_portal")
