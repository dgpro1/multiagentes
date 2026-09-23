"""Outbound webhooks: subscriptions and their delivery log.

A subscription belongs to one integration and names the events it wants;
each matching event fans out into a delivery row per subscription. The
worker sends them with HMAC-SHA256 signatures and a 5/15/15/60-minute
retry ladder, and the log behind them can be replayed by hand.
"""
from alembic import op
import sqlalchemy as sa

revision = "0056_outbound_webhooks"
down_revision = "0055_api_idempotency"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("integration_id", sa.Uuid(), sa.ForeignKey("api_integrations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_webhook_subscriptions_integration_id", "webhook_subscriptions", ["integration_id"])
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subscription_id", sa.Uuid(), sa.ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event", sa.String(60), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_webhook_deliveries_subscription_id", "webhook_deliveries", ["subscription_id"])


def downgrade():
    # contract: reviewed. Both tables are log and configuration the previous
    # release never reads; going back drops subscriptions and their log, and
    # nothing else changes.
    op.drop_index("ix_webhook_deliveries_subscription_id", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_webhook_subscriptions_integration_id", table_name="webhook_subscriptions")
    op.drop_table("webhook_subscriptions")
