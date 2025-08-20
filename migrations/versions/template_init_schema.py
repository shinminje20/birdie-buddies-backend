"""init schema

Revision ID: 0001_init
Revises:
Create Date: 2025-08-09

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg


# revision identifiers, used by Alembic.
revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable citext for case-insensitive email
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    # users
    op.create_table(
        "users",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", pg.CITEXT(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status in ('active','disabled')", name="ck_users_users_status"),
    )
    op.create_unique_constraint("uq_users_email", "users", ["email"])
    op.create_unique_constraint("uq_users_phone", "users", ["phone"])

    # sessions
    op.create_table(
        "sessions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("starts_at", pg.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("fee_cents", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'scheduled'")),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("capacity > 0", name="ck_sessions_sessions_capacity_pos"),
        sa.CheckConstraint("fee_cents >= 0", name="ck_sessions_sessions_fee_nonneg"),
        sa.CheckConstraint("status in ('scheduled','closed','canceled')", name="ck_sessions_sessions_status"),
    )
    op.create_index("ix_sessions_starts_at", "sessions", ["starts_at"], unique=False)

    # registrations
    op.create_table(
        "registrations",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("session_id", pg.UUID(as_uuid=True), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("host_user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("guest_names", pg.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")),
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'waitlisted'")),
        sa.Column("waitlist_pos", sa.Integer(), nullable=True),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("canceled_at", pg.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("seats >= 1 AND seats <= 3", name="ck_registrations_registrations_seats_range"),
        sa.CheckConstraint("state in ('confirmed','waitlisted','canceled')", name="ck_registrations_registrations_state"),
    )
    # Helpful indexes, including partial unique: one active registration per host per session
    op.create_index(
        "uq_reg_active_once",
        "registrations",
        ["session_id", "host_user_id"],
        unique=True,
        postgresql_where=sa.text("state <> 'canceled'"),
    )
    op.create_index(
        "ix_reg_session_state_pos",
        "registrations",
        ["session_id", "state", "waitlist_pos"],
        unique=False,
    )

    # ledger_entries
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_id", pg.UUID(as_uuid=True), sa.ForeignKey("sessions.id"), nullable=True),
        sa.Column("registration_id", pg.UUID(as_uuid=True), sa.ForeignKey("registrations.id"), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'posted'")),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "kind in ('deposit_in','fee_hold','fee_capture','hold_release','refund','penalty')",
            name="ck_ledger_ledger_kind",
        ),
        sa.CheckConstraint("status in ('posted','void')", name="ck_ledger_ledger_status"),
    )
    op.create_index("ix_ledger_user", "ledger_entries", ["user_id"], unique=False)
    op.create_index("ix_ledger_session", "ledger_entries", ["session_id"], unique=False)
    op.create_index("ix_ledger_registration", "ledger_entries", ["registration_id"], unique=False)

    # wallets
    op.create_table(
        "wallets",
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True, nullable=False),
        sa.Column("posted_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("holds_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("wallets")
    op.drop_index("ix_ledger_registration", table_name="ledger_entries")
    op.drop_index("ix_ledger_session", table_name="ledger_entries")
    op.drop_index("ix_ledger_user", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index("ix_reg_session_state_pos", table_name="registrations")
    op.drop_index("uq_reg_active_once", table_name="registrations")
    op.drop_table("registrations")
    op.drop_index("ix_sessions_starts_at", table_name="sessions")
    op.drop_table("sessions")
    op.drop_constraint("uq_users_phone", "users", type_="unique")
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_table("users")
    # leave citext extension in place (harmless); drop if you insist:
    # op.execute("DROP EXTENSION IF EXISTS citext")
