"""Auto migration

Revision ID: 651db394abf0
Revises: 0013_fees_back_to_integer
Create Date: 2025-08-20 13:15:23.376593

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '651db394abf0'
down_revision = '0013_fees_back_to_integer'
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
