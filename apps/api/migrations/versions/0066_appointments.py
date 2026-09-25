"""Appointments and professional assignment notes.

Adds the appointments table for scheduled appointments and tasks, and adds
assignment_notes to the professionals table.

Revision ID: 0066_appointments
Revises: 0065_services_and_business
"""

import sqlalchemy as sa
from alembic import op


revision = "0066_appointments"
down_revision = "0065_services_and_business"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("professionals", sa.Column("assignment_notes", sa.Text(), nullable=True))

    op.create_table(
        "appointments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agency_id", sa.Uuid(), sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("contact_id", sa.Uuid(), sa.ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("professional_id", sa.Uuid(), sa.ForeignKey("professionals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("service_id", sa.Uuid(), sa.ForeignKey("services.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="confirmed"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_role", sa.String(32), nullable=False, server_default="operator"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_appointments_agency_id", "appointments", ["agency_id"])
    op.create_index("ix_appointments_client_id", "appointments", ["client_id"])
    op.create_index("ix_appointments_conversation_id", "appointments", ["conversation_id"])
    op.create_index("ix_appointments_contact_id", "appointments", ["contact_id"])
    op.create_index("ix_appointments_professional_id", "appointments", ["professional_id"])
    op.create_index("ix_appointments_service_id", "appointments", ["service_id"])
    op.create_index("ix_appointments_start_time", "appointments", ["start_time"])
    op.create_index("ix_appointments_end_time", "appointments", ["end_time"])
    op.create_index("ix_appointments_status", "appointments", ["status"])


def downgrade() -> None:
    # contract: reviewed. Dropping the table and professional assignment notes
    # only loses appointments entered since this release.
    op.drop_index("ix_appointments_status", table_name="appointments")
    op.drop_index("ix_appointments_end_time", table_name="appointments")
    op.drop_index("ix_appointments_start_time", table_name="appointments")
    op.drop_index("ix_appointments_service_id", table_name="appointments")
    op.drop_index("ix_appointments_professional_id", table_name="appointments")
    op.drop_index("ix_appointments_contact_id", table_name="appointments")
    op.drop_index("ix_appointments_conversation_id", table_name="appointments")
    op.drop_index("ix_appointments_client_id", table_name="appointments")
    op.drop_index("ix_appointments_agency_id", table_name="appointments")
    op.drop_table("appointments")
    op.drop_column("professionals", "assignment_notes")
