"""Change fee_cents from Integer to Float

Revision ID: 0002_fee_cents_float
Revises: 0001_init
Create Date: 2025-08-11
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0003_fee_cents_float"
down_revision = "4df7b4cb0789"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "sessions",
        "fee_cents",
        existing_type=sa.Integer(),
        type_=sa.Float(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "sessions",
        "fee_cents",
        existing_type=sa.Float(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
