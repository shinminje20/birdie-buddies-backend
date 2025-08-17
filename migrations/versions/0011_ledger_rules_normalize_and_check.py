"""Normalize historical ledger rows and enforce canonical kind/status/sign rules.

Revision ID: 0011_ledger_rules_normalize_and_check
Revises: 0010_reg_is_host_and_unique
Create Date: 2025-08-17
"""
from alembic import op

revision = "0011_ledger_rules"
down_revision = "0010_reg_is_host_and_unique"
branch_labels = None
depends_on = None

def upgrade():
    # 0) If you had a previous CK installed (partial attempt), drop it first (idempotent).
    op.execute("ALTER TABLE ledger_entries DROP CONSTRAINT IF EXISTS ck_ledger_kind_status_amount")
    op.execute("ALTER TABLE ledger_entries DROP CONSTRAINT IF EXISTS ck_ledger_entries_ck_ledger_ledger_kind")
    op.execute("ALTER TABLE ledger_entries DROP CONSTRAINT IF EXISTS ck_ledger_entries_kind_status")

    # 1) Ensure amount_cents is INTEGER (cast if needed; no-op if already integer)
    op.execute("""
        ALTER TABLE ledger_entries
        ALTER COLUMN amount_cents TYPE integer
        USING round(amount_cents)::integer
    """)

    # 2) Normalize statuses to match canonical mapping
    #    - hold          -> 'held'
    #    - everything else -> 'posted'
    op.execute("UPDATE ledger_entries SET status = 'held'  WHERE kind = 'hold'         AND status <> 'held'")
    op.execute("UPDATE ledger_entries SET status = 'posted' WHERE kind IN ('hold_release','deposit_in','refund','fee_capture','penalty') AND status <> 'posted'")

    # 3) Normalize signs (positive/negative) to match canonical mapping
    #    Positive kinds:  hold, deposit_in, refund
    op.execute("""
        UPDATE ledger_entries
        SET amount_cents = abs(amount_cents)
        WHERE kind IN ('hold','deposit_in','refund') AND amount_cents <= 0
    """)
    #    Negative kinds:  hold_release, fee_capture, penalty
    op.execute("""
        UPDATE ledger_entries
        SET amount_cents = -abs(amount_cents)
        WHERE kind IN ('hold_release','fee_capture','penalty') AND amount_cents >= 0
    """)

    # 4) Install a canonical CHECK (NOT overly strict on registration_id for historical rows)
    #    If your project uses 'reserved' instead of 'held', swap the literal.
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

def downgrade():
    op.execute("ALTER TABLE ledger_entries DROP CONSTRAINT IF EXISTS ck_ledger_kind_status_amount")
