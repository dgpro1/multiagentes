"""Where each client's data plane lives, and its tenant schema version.

Adds clients.data_mode ("central" for every existing client, so nothing moves)
and client_data_stores.schema_version (the last migrations_tenant/ revision
applied in the client's own database).

Revision ID: 0072_client_data_mode
Revises: 0071_client_data_stores
"""

import sqlalchemy as sa
from alembic import op


revision = "0072_client_data_mode"
down_revision = "0071_client_data_stores"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("data_mode", sa.String(20), nullable=False, server_default="central"))
    op.add_column("client_data_stores", sa.Column("schema_version", sa.String(40), nullable=False, server_default=""))


def downgrade() -> None:
    # contract: reviewed — the previous release keeps every client central; a
    # client switched to its own database must be switched back (and its data
    # copied back) before downgrading, which the switch in tenant_copy.py does.
    op.drop_column("client_data_stores", "schema_version")
    op.drop_column("clients", "data_mode")
