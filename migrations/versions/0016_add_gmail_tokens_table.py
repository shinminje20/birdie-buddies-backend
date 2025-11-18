"""Add gmail tokens table

Revision ID: 0016_add_gmail_tokens_table
Revises: 0015_deposit_in_allow_negative
Create Date: 2025-11-17 18:46:46.327734

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

# revision identifiers, used by Alembic.
revision = '0016_add_gmail_tokens_table'
down_revision = '0015_deposit_in_allow_negative'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'gmail_tokens',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('email', pg.CITEXT(), nullable=False),
        sa.Column('refresh_token', sa.Text(), nullable=False),
        sa.Column('history_id', sa.Text(), nullable=True),
        sa.Column('watch_expiration', pg.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id', name='pk_gmail_tokens'),
        sa.UniqueConstraint('email', name='uq_gmail_tokens_email')
    )


def downgrade() -> None:
    op.drop_table('gmail_tokens')
