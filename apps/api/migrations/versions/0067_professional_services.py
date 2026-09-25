"""Professional services association table.

Adds professional_services table to link professionals to the services they perform (M:N).

Revision ID: 0067_professional_services
Revises: 0066_appointments
"""

import sqlalchemy as sa
from alembic import op


revision = "0067_professional_services"
down_revision = "0066_appointments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "professional_services",
        sa.Column("professional_id", sa.Uuid(), sa.ForeignKey("professionals.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("service_id", sa.Uuid(), sa.ForeignKey("services.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_professional_services_service_id", "professional_services", ["service_id"])


def downgrade() -> None:
    op.drop_index("ix_professional_services_service_id", table_name="professional_services")
    op.drop_table("professional_services")
