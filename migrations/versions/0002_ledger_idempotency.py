"""add idempotency_key to ledger_entries

Revision ID: 0002_ledger_idempotency
Revises: 0001_init
Create Date: 2025-08-10
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0002_ledger_idempotency"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ledger_entries", sa.Column("idempotency_key", sa.Text(), nullable=True))
    # Unique with NULLs allowed (multiple NULLs ok; any non-NULL must be unique)
    op.create_unique_constraint(
        "uq_ledger_idempotency_key",
        "ledger_entries",
        ["idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_ledger_idempotency_key", "ledger_entries", type_="unique")
    op.drop_column("ledger_entries", "idempotency_key")
