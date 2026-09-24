"""A channel connection can be started by a client's portal admin.

``social_oauth_states.user_id`` names the agency user who started a provider
connection. A portal admin is not a row of ``users``, so the column now allows
NULL: such a state records no agency user and carries the portal address to
return to in ``next_url``, as it always carried the panel's. Rows written before
this migration keep their user. The same table holds the return address of a
WhatsApp API connection under its own provider tag.

Revision ID: 0060_portal_channel_flows
Revises: 0059_portal_features
"""

from alembic import op


revision = "0060_portal_channel_flows"
down_revision = "0059_portal_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Relaxes a constraint only: the previous release keeps writing a user id.
    op.alter_column("social_oauth_states", "user_id", nullable=True)


def downgrade() -> None:
    # contract: reviewed. States started from a portal have no agency user and
    # cannot exist under the old constraint. They are short-lived (minutes) and
    # only carry a return address, so dropping them costs a re-started connection.
    op.execute("DELETE FROM social_oauth_states WHERE user_id IS NULL")
    op.alter_column("social_oauth_states", "user_id", nullable=False)
