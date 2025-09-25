"""Allow deposit_in to be negative or positive"""

from alembic import op

# revision identifiers
revision = "0015_deposit_in_allow_negative"
down_revision = "0014_user_avatar"
branch_labels = None
depends_on = None


def upgrade():
    # Drop the previous check constraint
    op.execute("ALTER TABLE ledger_entries DROP CONSTRAINT IF EXISTS ck_ledger_kind_status_amount")

    # Recreate with relaxed rule for deposit_in
    op.execute("""
        ALTER TABLE ledger_entries
        ADD CONSTRAINT ck_ledger_kind_status_amount
        CHECK (
            (kind = 'deposit_in'  AND status = 'posted' AND amount_cents != 0) OR
            (kind = 'refund'      AND status = 'posted' AND amount_cents > 0) OR
            (kind = 'fee_capture' AND status = 'posted' AND amount_cents < 0) OR
            (kind = 'penalty'     AND status = 'posted' AND amount_cents < 0) OR
            (kind = 'hold'        AND status = 'held'   AND amount_cents > 0) OR
            (kind = 'hold_release'AND status = 'posted' AND amount_cents < 0)
        )
    """)


def downgrade():
    # Revert back to strict positive-only deposit_in
    op.execute("ALTER TABLE ledger_entries DROP CONSTRAINT IF EXISTS ck_ledger_kind_status_amount")
    op.execute("""
        ALTER TABLE ledger_entries
        ADD CONSTRAINT ck_ledger_kind_status_amount
        CHECK (
            (kind = 'deposit_in'  AND status = 'posted' AND amount_cents > 0) OR
            (kind = 'refund'      AND status = 'posted' AND amount_cents > 0) OR
            (kind = 'fee_capture' AND status = 'posted' AND amount_cents < 0) OR
            (kind = 'penalty'     AND status = 'posted' AND amount_cents < 0) OR
            (kind = 'hold'        AND status = 'held'   AND amount_cents > 0) OR
            (kind = 'hold_release'AND status = 'posted' AND amount_cents < 0)
        )
    """)
