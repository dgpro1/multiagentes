"""Message attachments can live in the client's own R2 bucket.

Adds message_attachments.storage_key and lets data be empty once the bytes
have been moved to the bucket. Existing rows keep their bytes until the
background sweep moves them (only for clients with a connected bucket).

Revision ID: 0070_attachment_storage_key
Revises: 0069_client_resources
"""

import sqlalchemy as sa
from alembic import op


revision = "0070_attachment_storage_key"
down_revision = "0069_client_resources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("message_attachments", sa.Column("storage_key", sa.String(300), nullable=True))
    op.alter_column("message_attachments", "data", existing_type=sa.LargeBinary(), nullable=True)


def downgrade() -> None:
    # contract: reviewed — rows whose bytes were moved to a client's bucket have no
    # data here; the previous release cannot read them either way, so they are
    # given an empty payload to satisfy NOT NULL rather than being deleted. The
    # files themselves remain in the client's bucket under storage_key.
    op.execute("UPDATE message_attachments SET data = ''::bytea WHERE data IS NULL")
    op.alter_column("message_attachments", "data", existing_type=sa.LargeBinary(), nullable=False)
    op.drop_column("message_attachments", "storage_key")
