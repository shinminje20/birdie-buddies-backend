"""drop unique phone constraint

Revision ID: 0017_drop_unique_phone
Revises: 0016
Create Date: 2025-11-18

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0017_drop_unique_phone'
down_revision = '0016_add_gmail_tokens_table'  # Set this to your latest migration revision
branch_labels = None
depends_on = None


def upgrade():
    # Drop the unique constraint on phone column
    op.drop_constraint("uq_users_phone", "users", type_="unique")


def downgrade():
    # Re-add the unique constraint if rolling back
    op.create_unique_constraint("uq_users_phone", "users", ["phone"])
