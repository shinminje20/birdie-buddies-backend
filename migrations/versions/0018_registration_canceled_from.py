"""add canceled_from_state to registrations

Revision ID: 0018_registration_canceled_from
Revises: 0017_drop_unique_phone
Create Date: 2025-02-11

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0018_registration_canceled_from"
down_revision = "0017_drop_unique_phone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "registrations",
        sa.Column(
            "canceled_from_state",
            sa.Text(),
            nullable=True,
            comment="State the registration was in when it was canceled (confirmed|waitlisted)",
        ),
    )


def downgrade() -> None:
    op.drop_column("registrations", "canceled_from_state")

