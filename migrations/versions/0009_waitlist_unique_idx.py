"""registration group_key for host+guest linkage

Revision ID: 0009_waitlist_unique_idx
Revises: 0008_registration_group_key
Create Date: 2025-08-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0009_waitlist_unique_idx"
down_revision = "0008_registration_group_key"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_reg_waitlist_pos
        ON registrations (session_id, waitlist_pos)
        WHERE state = 'waitlisted'
    """)
def downgrade():
    op.execute("DROP INDEX IF EXISTS ux_reg_waitlist_pos")

