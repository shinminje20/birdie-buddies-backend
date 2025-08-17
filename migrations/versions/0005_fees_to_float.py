from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "0005_fees_to_float"
down_revision = "c89f2c1bc5c3"
branch_labels = None
depends_on = None

def upgrade():
    op.alter_column("ledger_entries", "amount_cents",
                    existing_type=sa.Integer(),
                    type_=sa.Float(),
                    existing_nullable=False,
                    postgresql_using="amount_cents::double precision")

def downgrade():
    op.alter_column("ledger_entries", "amount_cents",
                    existing_type=sa.Float(),
                    type_=sa.Integer(),
                    existing_nullable=False,
                    postgresql_using="round(amount_cents)::int")