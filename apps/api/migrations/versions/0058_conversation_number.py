"""Every conversation gets a short number, unique per client.

A lead in the panel is a conversation, and people refer to it as "#12" the way
they do in Kommo, so it lives at ``/inbox/12`` instead of behind a UUID. The
number counts up per client from 1. ``clients.conversation_seq`` holds the last
number handed out; the application bumps it with a row lock each time a
conversation is inserted.

Existing conversations are numbered oldest first within their client
(``created_at``, then ``id`` to break ties), and each client's counter starts at
its highest number.

Revision ID: 0058_conversation_number
Revises: 0057_single_agent_prompt
"""

import sqlalchemy as sa
from alembic import op


revision = "0058_conversation_number"
down_revision = "0057_single_agent_prompt"
branch_labels = None
depends_on = None


BACKFILL_NUMBERS = """
    UPDATE conversations SET number = numbered.n
    FROM (
        SELECT id, row_number() OVER (PARTITION BY client_id ORDER BY created_at, id) AS n
        FROM conversations
    ) AS numbered
    WHERE conversations.id = numbered.id
"""

SEED_COUNTERS = """
    UPDATE clients SET conversation_seq = COALESCE(
        (SELECT max(number) FROM conversations WHERE conversations.client_id = clients.id), 0
    )
"""


def upgrade() -> None:
    # Purely additive: two new columns and a unique constraint. The previous
    # release never writes `number`, so it keeps working only until the first
    # insert after this runs; deploy the two together.
    op.add_column("clients", sa.Column("conversation_seq", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("conversations", sa.Column("number", sa.Integer(), nullable=True))
    op.execute(BACKFILL_NUMBERS)
    op.execute(SEED_COUNTERS)
    op.alter_column("conversations", "number", existing_type=sa.Integer(), nullable=False)
    op.create_unique_constraint("uq_conversations_client_number", "conversations", ["client_id", "number"])


def downgrade() -> None:
    # contract: reviewed. Dropping the number loses only the derived "#12"
    # labels; the rows and their UUIDs are untouched, and the numbers are
    # recomputed from created_at if the upgrade is applied again.
    op.drop_constraint("uq_conversations_client_number", "conversations", type_="unique")
    op.drop_column("conversations", "number")
    op.drop_column("clients", "conversation_seq")
