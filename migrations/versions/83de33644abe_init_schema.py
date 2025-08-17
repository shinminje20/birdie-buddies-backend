"""Auto migration

Revision ID: 83de33644abe
Revises: 0001_init
Create Date: 2025-08-10 15:05:06.896530

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '83de33644abe'
down_revision = '0001_init'
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
