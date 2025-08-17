"""Auto migration

Revision ID: 4df7b4cb0789
Revises: 20fed236a7eb
Create Date: 2025-08-11 15:14:22.564789

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '4df7b4cb0789'
down_revision = '20fed236a7eb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
