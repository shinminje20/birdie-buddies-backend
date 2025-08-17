"""index for auto-close scan

Revision ID: 0004_sessions_idx_autoclose
Revises: 0003_events_outbox
Create Date: 2025-08-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_sessions_idx_autoclose"
down_revision = "0006_events_outbox"
branch_labels = None
depends_on = None

def upgrade():
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sessions_scheduled_start "
        "ON sessions (starts_at) WHERE status = 'scheduled'"
    )

def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_sessions_scheduled_start")
