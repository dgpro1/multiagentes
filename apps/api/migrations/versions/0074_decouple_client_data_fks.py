"""Central tables no longer hold foreign keys into a client's data.

A conversation (and its messages) may now live in the client's own
database, so social_outbox and usage_records keep plain ids. The ON DELETE
behaviour moves into the application (models._forget_central_references).

Revision ID: 0074_decouple_client_data_fks
Revises: 0073_hunterai_pending_inbound
"""

from alembic import op


revision = "0074_decouple_client_data_fks"
down_revision = "0073_hunterai_pending_inbound"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # contract: reviewed — only constraints are dropped, no column or row; the
    # previous release keeps working (it never relied on the constraint to
    # insert), and deletes are mirrored by the application from this release.
    op.drop_constraint("social_outbox_conversation_id_fkey", "social_outbox", type_="foreignkey")
    op.drop_constraint("social_outbox_message_id_fkey", "social_outbox", type_="foreignkey")
    op.drop_constraint("fk_usage_records_conversation", "usage_records", type_="foreignkey")
    op.drop_constraint("fk_usage_records_message", "usage_records", type_="foreignkey")


def downgrade() -> None:
    # contract: reviewed — rows pointing at conversations that moved to a
    # client's database would violate the restored constraints: clear or
    # remove them first (the conversations themselves are not in this database).
    op.execute("DELETE FROM social_outbox WHERE conversation_id NOT IN (SELECT id FROM conversations)")
    op.execute("UPDATE usage_records SET conversation_id = NULL WHERE conversation_id NOT IN (SELECT id FROM conversations)")
    op.execute("UPDATE usage_records SET message_id = NULL WHERE message_id NOT IN (SELECT id FROM messages)")
    op.create_foreign_key("social_outbox_conversation_id_fkey", "social_outbox", "conversations", ["conversation_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("social_outbox_message_id_fkey", "social_outbox", "messages", ["message_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("fk_usage_records_conversation", "usage_records", "conversations", ["conversation_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_usage_records_message", "usage_records", "messages", ["message_id"], ["id"], ondelete="SET NULL")
