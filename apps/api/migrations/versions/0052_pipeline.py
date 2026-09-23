"""Sales pipeline: per-client stages, and the stage/value a conversation carries."""
from alembic import op
import sqlalchemy as sa

revision = "0052_pipeline"
down_revision = "0051_calendar_members"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pipeline_stages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("client_id", sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("color", sa.String(16), nullable=False, server_default="#2f6df0"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id", "name", name="uq_pipeline_stages_client_name"),
    )
    op.create_index("ix_pipeline_stages_client_id", "pipeline_stages", ["client_id"])
    # conversations before the channel tables, matching this repo's lock order.
    op.add_column("conversations", sa.Column(
        "pipeline_stage_id", sa.Uuid(), sa.ForeignKey("pipeline_stages.id", ondelete="SET NULL"), nullable=True
    ))
    op.add_column("conversations", sa.Column("deal_value", sa.Numeric(12, 2), nullable=True))
    op.create_index("ix_conversations_pipeline_stage_id", "conversations", ["pipeline_stage_id"])


def downgrade():
    # contract: reviewed. Both columns and the table are new in this release;
    # the previous release never reads them, so dropping them on the way back
    # loses only pipeline stages and deal values set since, which must be
    # re-entered after upgrading again.
    op.drop_index("ix_conversations_pipeline_stage_id", table_name="conversations")
    op.drop_column("conversations", "deal_value")
    op.drop_column("conversations", "pipeline_stage_id")
    op.drop_index("ix_pipeline_stages_client_id", table_name="pipeline_stages")
    op.drop_table("pipeline_stages")
