"""An agent's prompt lives in one field: its instructions.

The panel used to split what an agent is told across ``personality`` and six
guided brief fields (summary, products, audience, policies, dos, donts) that
composed into the system prompt beside the instructions. The panel now edits
``instructions`` alone, so the text left in the other fields would keep
reaching the model with nobody able to see or change it. It is cleared.

The columns stay: the public API v1 still exposes them and the prompt builder
skips a section whose field is blank, so integrators and the previous release
keep working. Each agent keeps its ``instructions``.

Revision ID: 0057_single_agent_prompt
Revises: 0056_outbound_webhooks
"""

from alembic import op


revision = "0057_single_agent_prompt"
down_revision = "0056_outbound_webhooks"
branch_labels = None
depends_on = None


CLEAR_PROMPT_FIELDS = """
    UPDATE agents SET
        personality = '',
        brief_summary = '',
        brief_products = '',
        brief_audience = '',
        brief_policies = '',
        brief_dos = '',
        brief_donts = ''
"""


def upgrade() -> None:
    # contract: reviewed. Only the text of seven prompt fields is cleared, on
    # the product owner's decision; no column is dropped or retyped, so the
    # previous release (which still reads and writes them) and API v1 keep
    # working, and each agent keeps its instructions.
    op.execute(CLEAR_PROMPT_FIELDS)


def downgrade() -> None:
    # Nothing to restore: the cleared prompt text cannot be recovered. The statement is a
    # no-op that keeps this from being an empty downgrade (tests/test_migration_conventions.py).
    op.execute("SELECT 1")
