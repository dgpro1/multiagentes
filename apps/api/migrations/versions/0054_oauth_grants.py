"""OAuth 2.0 grants on the API credential tables.

An integration doubles as an OAuth client: ``oauth_client_id`` names it in
the open, the secret hash checks it at the token endpoint, and the redirect
list pins where codes may go. Grants live in ``api_tokens`` by kind
(auth_code, access, refresh) sharing a ``grant_id``; a token may also carry
its own granted subset, falling back to the integration's scopes when empty.
"""
from alembic import op
import sqlalchemy as sa

revision = "0054_oauth_grants"
down_revision = "0053_api_integrations"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("api_integrations", sa.Column("oauth_client_id", sa.String(40), nullable=True))
    op.add_column("api_integrations", sa.Column("oauth_client_secret_hash", sa.String(64), nullable=True))
    op.add_column("api_integrations", sa.Column("oauth_redirect_uris", sa.JSON(), nullable=False, server_default="[]"))
    op.create_unique_constraint("uq_api_integrations_oauth_client_id", "api_integrations", ["oauth_client_id"])
    op.add_column("api_tokens", sa.Column("scopes", sa.JSON(), nullable=True))
    op.add_column("api_tokens", sa.Column("redirect_uri", sa.String(500), nullable=True))
    op.add_column("api_tokens", sa.Column("grant_id", sa.Uuid(), nullable=True))
    op.create_index("ix_api_tokens_grant_id", "api_tokens", ["grant_id"])


def downgrade():
    # contract: reviewed. OAuth columns only; the previous release ignores
    # them, so going back orphans unfinished grants while long-lived tokens
    # keep working exactly as before.
    op.drop_index("ix_api_tokens_grant_id", table_name="api_tokens")
    op.drop_column("api_tokens", "grant_id")
    op.drop_column("api_tokens", "redirect_uri")
    op.drop_column("api_tokens", "scopes")
    op.drop_constraint("uq_api_integrations_oauth_client_id", "api_integrations", type_="unique")
    op.drop_column("api_integrations", "oauth_redirect_uris")
    op.drop_column("api_integrations", "oauth_client_secret_hash")
    op.drop_column("api_integrations", "oauth_client_id")
