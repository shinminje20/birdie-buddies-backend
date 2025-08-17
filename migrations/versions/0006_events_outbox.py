"""events_outbox for durable publish

Revision ID: 0006_events_outbox
Revises: 0005_fees_to_float
Create Date: 2025-08-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0006_events_outbox"
down_revision = "0005_fees_to_float"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("payload", pg.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("available_at", pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("sent_at", pg.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    # Fast scan of unsent, ready events
    op.create_index(
        "ix_outbox_ready",
        "events_outbox",
        ["available_at", "id"],
        unique=False,
        postgresql_where=sa.text("sent_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_ready", table_name="events_outbox")
    op.drop_table("events_outbox")
