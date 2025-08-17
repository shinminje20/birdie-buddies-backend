"""Auto migration

Revision ID: 20fed236a7eb
Revises: 0002_ledger_idempotency, 83de33644abe
Create Date: 2025-08-10 16:06:16.517965

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20fed236a7eb'
down_revision = ('0002_ledger_idempotency', '83de33644abe')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
