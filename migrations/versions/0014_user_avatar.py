"""user avatar/deleted_at + allow 'hold' in ledger kind"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0014_user_avatar"
down_revision = "0013_fees_back_to_integer"  # <-- set to your last revision id
branch_labels = None
depends_on = None

def upgrade():
    # Users: avatar_url, deleted_at (+ index)
    op.add_column("users", sa.Column("avatar_url", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_deleted_at", "users", ["deleted_at"])

    # Ledger: relax kind check to include 'hold' (keep 'fee_hold' for b/c)
    # We don't know the exact auto name in older revs (you had a different ck name in logs),
    # so drop with raw SQL + IF EXISTS for both possible names, then create a new ck.
    op.execute("ALTER TABLE ledger_entries DROP CONSTRAINT IF EXISTS ck_ledger_entries_ledger_kind")
    op.execute("ALTER TABLE ledger_entries DROP CONSTRAINT IF EXISTS ck_ledger_entries_ck_ledger_ledger_kind")

    op.create_check_constraint(
        "ledger_kind",
        "ledger_entries",
        "kind in ('deposit_in','fee_hold','hold','fee_capture','hold_release','refund','penalty')",
    )

def downgrade():
    # revert ledger constraint to original (no 'hold')
    op.execute("ALTER TABLE ledger_entries DROP CONSTRAINT IF EXISTS ck_ledger_entries_ledger_kind")
    op.create_check_constraint(
        "ledger_kind",
        "ledger_entries",
        "kind in ('deposit_in','fee_hold','fee_capture','hold_release','refund','penalty')",
    )

    # drop users additions
    op.drop_index("ix_users_deleted_at", table_name="users")
    op.drop_column("users", "deleted_at")
    op.drop_column("users", "avatar_url")
