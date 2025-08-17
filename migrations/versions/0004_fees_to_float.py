from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "0004_wallets_float"
down_revision = "4df7b4cb0789"
branch_labels = None
depends_on = None

def upgrade():
    op.alter_column("wallets", "posted_cents",
                    existing_type=sa.BigInteger(),
                    type_=sa.Float(),
                    existing_nullable=False,
                    postgresql_using="holds_cents::double precision")
    op.alter_column("wallets", "holds_cents",
                    existing_type=sa.BigInteger(),
                    type_=sa.Float(),
                    existing_nullable=False,
                    postgresql_using="holds_cents::double precision")

def downgrade():
    op.alter_column("wallets", "posted_cents",
                    existing_type=sa.Float(),
                    type_=sa.BigInteger(),
                    existing_nullable=False,
                    postgresql_using="round(holds_cents)::bigint")
    op.alter_column("wallets", "holds_cents",
                    existing_type=sa.Float(),
                    type_=sa.BigInteger(),
                    existing_nullable=False,
                    postgresql_using="round(holds_cents)::bigint")