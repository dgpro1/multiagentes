"""Per-line feature toggles for the Evolution driver: groups and calls."""
from alembic import op
import sqlalchemy as sa

revision = "0050_whatsapp_line_toggles"
down_revision = "0049_messaging_provider"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("whatsapp_channels", sa.Column("groups_enabled", sa.Boolean(), nullable=True))
    op.add_column("whatsapp_channels", sa.Column("calls_enabled", sa.Boolean(), nullable=True))
    op.add_column("whatsapp_channels", sa.Column("calls_message", sa.String(200), nullable=True))
    op.execute("UPDATE whatsapp_channels SET groups_enabled = false WHERE groups_enabled IS NULL")
    op.execute("UPDATE whatsapp_channels SET calls_enabled = false WHERE calls_enabled IS NULL")
    op.alter_column("whatsapp_channels", "groups_enabled", nullable=False, server_default=sa.text("false"))
    op.alter_column("whatsapp_channels", "calls_enabled", nullable=False, server_default=sa.text("false"))


def downgrade():
    op.drop_column("whatsapp_channels", "calls_message")
    op.drop_column("whatsapp_channels", "calls_enabled")
    op.drop_column("whatsapp_channels", "groups_enabled")
