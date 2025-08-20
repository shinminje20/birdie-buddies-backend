# migrations/versions/0013_fees_back_to_integer.py
from alembic import op
import sqlalchemy as sa

# --- identifiers ---
revision = "0013_fees_back_to_integer"
down_revision = "0012_ledger_drop_legacy_status"   # <-- set this to your current head if different
branch_labels = None
depends_on = None


def upgrade():
    # 1) Drop wallet defaults to avoid cast conflicts
    op.execute("ALTER TABLE wallets ALTER COLUMN posted_cents DROP DEFAULT")
    op.execute("ALTER TABLE wallets ALTER COLUMN holds_cents DROP DEFAULT")

    # 2) Cast float -> integer (round to nearest cent)
    op.alter_column(
        "sessions",
        "fee_cents",
        existing_type=sa.Float(),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="round(fee_cents)::integer",
    )

    op.alter_column(
        "ledger_entries",
        "amount_cents",
        existing_type=sa.Float(),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="round(amount_cents)::integer",
    )

    op.alter_column(
        "wallets",
        "posted_cents",
        existing_type=sa.Float(),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="round(posted_cents)::integer",
    )

    op.alter_column(
        "wallets",
        "holds_cents",
        existing_type=sa.Float(),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="round(holds_cents)::integer",
    )

    # 3) Restore wallet defaults (0 cents)
    op.execute("ALTER TABLE wallets ALTER COLUMN posted_cents SET DEFAULT 0")
    op.execute("ALTER TABLE wallets ALTER COLUMN holds_cents SET DEFAULT 0")


def downgrade():
    # Downgrade back to float (double precision)

    # Drop wallet defaults to avoid cast issues
    op.execute("ALTER TABLE wallets ALTER COLUMN posted_cents DROP DEFAULT")
    op.execute("ALTER TABLE wallets ALTER COLUMN holds_cents DROP DEFAULT")

    op.alter_column(
        "sessions",
        "fee_cents",
        existing_type=sa.Integer(),
        type_=sa.Float(),
        existing_nullable=False,
        postgresql_using="fee_cents::double precision",
    )

    op.alter_column(
        "ledger_entries",
        "amount_cents",
        existing_type=sa.Integer(),
        type_=sa.Float(),
        existing_nullable=False,
        postgresql_using="amount_cents::double precision",
    )

    op.alter_column(
        "wallets",
        "posted_cents",
        existing_type=sa.Integer(),
        type_=sa.Float(),
        existing_nullable=False,
        postgresql_using="posted_cents::double precision",
    )

    op.alter_column(
        "wallets",
        "holds_cents",
        existing_type=sa.Integer(),
        type_=sa.Float(),
        existing_nullable=False,
        postgresql_using="holds_cents::double precision",
    )

    # Restore wallet defaults
    op.execute("ALTER TABLE wallets ALTER COLUMN posted_cents SET DEFAULT 0")
    op.execute("ALTER TABLE wallets ALTER COLUMN holds_cents SET DEFAULT 0")
