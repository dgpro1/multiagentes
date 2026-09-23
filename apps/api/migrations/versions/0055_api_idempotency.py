"""Idempotency records for versioned API writes.

A repeated POST with the same key and body replays the stored answer
instead of acting twice; the rows are keyed per credential owner so one
integration can never replay another's request.
"""
from alembic import op
import sqlalchemy as sa

revision = "0055_api_idempotency"
down_revision = "0054_oauth_grants"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "api_idempotency_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agency_id", sa.Uuid(), sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_key", sa.String(80), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("endpoint", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_api_idempotency_keys_agency_id", "api_idempotency_keys", ["agency_id"])
    op.create_unique_constraint(
        "uq_api_idempotency_owner_key", "api_idempotency_keys", ["agency_id", "owner_key", "key_hash"]
    )


def downgrade():
    # contract: reviewed. The table only holds recent write receipts; the
    # previous release never reads it, so going back only loses replay
    # memory and every retried request simply acts again.
    op.drop_constraint("uq_api_idempotency_owner_key", "api_idempotency_keys", type_="unique")
    op.drop_index("ix_api_idempotency_keys_agency_id", table_name="api_idempotency_keys")
    op.drop_table("api_idempotency_keys")
