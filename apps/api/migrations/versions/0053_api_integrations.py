"""API integrations and their tokens: the credentials a third party uses.

An integration is the container (who it acts for, what it may do) and a token
is the secret. OAuth 2.0 lands on the same two tables later; this release only
issues long-lived tokens, so ``api_tokens.kind`` already names the kind.
"""
from alembic import op
import sqlalchemy as sa

revision = "0053_api_integrations"
down_revision = "0052_pipeline"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "api_integrations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agency_id", sa.Uuid(), sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_api_integrations_agency_id", "api_integrations", ["agency_id"])
    op.create_index("ix_api_integrations_client_id", "api_integrations", ["client_id"])
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("integration_id", sa.Uuid(), sa.ForeignKey("api_integrations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="long_lived"),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("token_prefix", sa.String(16), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_api_tokens_integration_id", "api_tokens", ["integration_id"])
    op.create_index("ix_api_tokens_token_hash", "api_tokens", ["token_hash"])
    op.create_unique_constraint("uq_api_tokens_hash", "api_tokens", ["token_hash"])


def downgrade():
    # contract: reviewed. Both tables are new in this release and the previous
    # one never reads them, so dropping them on the way back loses the API
    # credentials, which must be created again after upgrading.
    op.drop_constraint("uq_api_tokens_hash", "api_tokens", type_="unique")
    op.drop_index("ix_api_tokens_token_hash", table_name="api_tokens")
    op.drop_index("ix_api_tokens_integration_id", table_name="api_tokens")
    op.drop_table("api_tokens")
    op.drop_index("ix_api_integrations_client_id", table_name="api_integrations")
    op.drop_index("ix_api_integrations_agency_id", table_name="api_integrations")
    op.drop_table("api_integrations")
