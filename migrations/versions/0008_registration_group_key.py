"""registration group_key for host+guest linkage

Revision ID: 0008_registration_group_key
Revises: 0007_ledger_user_paging_idx
Create Date: 2025-08-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0008_registration_group_key"
down_revision = "0007_sessions_idx_autoclose"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column(
        "registrations",
        sa.Column("group_key", pg.UUID(as_uuid=True), nullable=True)
    )
    op.create_index("ix_reg_group_key", "registrations", ["group_key"], unique=False)

def downgrade():
    op.drop_index("ix_reg_group_key", table_name="registrations")
    op.drop_column("registrations", "group_key")
