"""Provider-side ids for the unified messaging channels (WhatsApp API, Instagram, Messenger)."""
from alembic import op
import sqlalchemy as sa

revision = "0049_messaging_provider"
down_revision = "0048_number_quality"
branch_labels = None
depends_on = None


def upgrade():
    # conversations first. Live requests lock conversations and then the
    # channel tables (loading the line a reply goes out on); locking the
    # channel tables first and then waiting on conversations deadlocked
    # against that traffic, with the migration as the victim every time.
    # Taking conversations first turns those requests into plain waiters.
    op.add_column("conversations", sa.Column("provider_conversation_id", sa.String(255), nullable=True))
    op.create_index("ix_conversations_provider_conversation_id", "conversations", ["provider_conversation_id"])
    op.add_column("clients", sa.Column("provider_profile_id", sa.String(128), nullable=True))
    op.add_column("whatsapp_cloud_channels", sa.Column("external_account_id", sa.String(128), nullable=False, server_default=""))
    op.add_column("whatsapp_cloud_channels", sa.Column("provider_profile_id", sa.String(128), nullable=True))
    op.add_column("social_channels", sa.Column("provider_profile_id", sa.String(128), nullable=True))


def downgrade():
    op.drop_column("social_channels", "provider_profile_id")
    op.drop_column("whatsapp_cloud_channels", "provider_profile_id")
    op.drop_column("whatsapp_cloud_channels", "external_account_id")
    op.drop_column("clients", "provider_profile_id")
    op.drop_index("ix_conversations_provider_conversation_id", table_name="conversations")
    op.drop_column("conversations", "provider_conversation_id")
