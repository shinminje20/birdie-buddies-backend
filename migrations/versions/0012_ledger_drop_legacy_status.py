# versions/0012_ledger_drop_legacy_status_ck.py
from alembic import op

revision = "0012_ledger_drop_legacy_status"
down_revision = "0011_ledger_rules"  # <- set to your real head
branch_labels = None
depends_on = None

def upgrade():
    # Drop any legacy checks (names can vary across envs)
    op.execute("ALTER TABLE ledger_entries DROP CONSTRAINT IF EXISTS ck_ledger_ledger_status")
    op.execute("ALTER TABLE ledger_entries DROP CONSTRAINT IF EXISTS ck_ledger_entries_ck_ledger_ledger_status")
    op.execute("ALTER TABLE ledger_entries DROP CONSTRAINT IF EXISTS ck_ledger_ledger_kind")
    op.execute("ALTER TABLE ledger_entries DROP CONSTRAINT IF EXISTS ck_ledger_entries_ck_ledger_ledger_kind")
    op.execute("ALTER TABLE ledger_entries DROP CONSTRAINT IF EXISTS ck_ledger_entries_kind_status")
    op.execute("ALTER TABLE ledger_entries DROP CONSTRAINT IF EXISTS ck_ledger_kind_status_amount")

    # Re-install your canonical combined check (matches your 0011 logic)
    op.execute("""
        ALTER TABLE ledger_entries
        ADD CONSTRAINT ck_ledger_kind_status_amount
        CHECK (
            (kind = 'deposit_in'   AND status = 'posted' AND amount_cents > 0) OR
            (kind = 'refund'       AND status = 'posted' AND amount_cents > 0) OR
            (kind = 'fee_capture'  AND status = 'posted' AND amount_cents < 0) OR
            (kind = 'penalty'      AND status = 'posted' AND amount_cents < 0) OR
            (kind = 'hold'         AND status = 'held'   AND amount_cents > 0) OR
            (kind = 'hold_release' AND status = 'posted' AND amount_cents < 0)
        )
    """)

def downgrade():
    op.execute("ALTER TABLE ledger_entries DROP CONSTRAINT IF EXISTS ck_ledger_kind_status_amount")
