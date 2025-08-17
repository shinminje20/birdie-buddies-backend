"""registrations.is_host and host-only unique index

Revision ID: 0010_reg_is_host_and_unique
Revises: 0009_waitlist_unique_idx
Create Date: 2025-08-17
"""
from alembic import op
import sqlalchemy as sa

# If your env autogenerates these, keep them; otherwise match your filenames
revision = "0010_reg_is_host_and_unique"
down_revision = "0009_waitlist_unique_idx"
branch_labels = None
depends_on = None

def upgrade():
    # 1) Add column (idempotent)
    op.execute("""
        ALTER TABLE registrations
        ADD COLUMN IF NOT EXISTS is_host boolean NOT NULL DEFAULT false
    """)

    # 2) Backfill: mark HOST rows (not guests).
    # Host if:
    #   - seats > 1  (old combined rows), OR
    #   - guest_names is NULL or empty (1-seat host)
    # Guest rows (new split) are seats=1 AND guest_names has at least one value.
    op.execute("""
        UPDATE registrations
        SET is_host = (
            (seats > 1)
            OR (guest_names IS NULL)
            OR (cardinality(guest_names) = 0)
        )
    """)

    # 3) Drop the old "one active registration per host per session" artifact if it exists.
    #    It may be a CONSTRAINT or an INDEX depending on how it was created.
    #    Adjust the name below if your actual name differs.
    op.execute("ALTER TABLE registrations DROP CONSTRAINT IF EXISTS uq_reg_active_once")
    op.execute("DROP INDEX IF EXISTS uq_reg_active_once")

    # 4) Create host-only unique partial index on active rows (not canceled)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_reg_host_one_active
        ON registrations (session_id, host_user_id)
        WHERE is_host = true AND state <> 'canceled'
    """)

def downgrade():
    # Drop new index and column (optionally recreate your old constraint if needed)
    op.execute("DROP INDEX IF EXISTS ux_reg_host_one_active")
    op.execute("ALTER TABLE registrations DROP COLUMN IF EXISTS is_host")
