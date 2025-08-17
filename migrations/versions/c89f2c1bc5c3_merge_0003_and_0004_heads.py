"""Auto migration

Revision ID: c89f2c1bc5c3
Revises: 0003_fee_cents_float, 0004_wallets_float
Create Date: 2025-08-11 15:58:23.720427

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c89f2c1bc5c3'
down_revision = ('0003_fee_cents_float', '0004_wallets_float')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
