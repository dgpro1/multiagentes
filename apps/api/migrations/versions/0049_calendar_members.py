"""Google Calendar: the people on a client's team whose calendars the agent books into."""
from alembic import op
import sqlalchemy as sa

revision = "0049_calendar_members"
down_revision = "0048_number_quality"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "calendar_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agency_id", sa.Uuid(), sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("role", sa.String(120), nullable=False, server_default=""),
        sa.Column("color", sa.String(16), nullable=False, server_default="#2f6df0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("google_email", sa.String(255), nullable=True),
        sa.Column("calendar_id", sa.String(255), nullable=False, server_default="primary"),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("encrypted_access_token", sa.Text(), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connect_token", sa.String(64), nullable=False, unique=True),
        sa.Column("connect_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_calendar_members_agency_id", "calendar_members", ["agency_id"])
    op.create_index("ix_calendar_members_client_id", "calendar_members", ["client_id"])
    op.create_table(
        "calendar_oauth_states",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("member_id", sa.Uuid(), sa.ForeignKey("calendar_members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connect_token", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_calendar_oauth_states_member_id", "calendar_oauth_states", ["member_id"])
    op.create_index("ix_calendar_oauth_states_expires_at", "calendar_oauth_states", ["expires_at"])


def downgrade():
    # contract: reviewed. These tables are new in this release; the previous
    # release never reads them, so dropping them on the way back loses only the
    # calendar connections made since, which must be made again after upgrading.
    op.drop_table("calendar_oauth_states")
    op.drop_table("calendar_members")
