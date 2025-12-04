from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, Boolean
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# ---------- Base & naming ----------
class Base(DeclarativeBase):
    # Keep index/constraint names stable for cleaner migrations
    metadata = sa.MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


# ---------- USERS ----------
class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # CITEXT gives case-insensitive unique email
    email: Mapped[str] = mapped_column(pg.CITEXT, nullable=False, unique=True)
    phone: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=False)

    is_admin: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False, server_default=sa.text("false"))
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default="active",
        server_default=sa.text("'active'"),
    )  # 'active' | 'disabled'

    created_at: Mapped[datetime] = mapped_column(
        pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    
    # MARK: added — profile picture url (served by /static or CDN later)
    avatar_url: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)

    # MARK: added — soft delete marker
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        pg.TIMESTAMP(timezone=True), nullable=True
    )
    
    __table_args__ = (
        CheckConstraint("status in ('active','disabled')", name="users_status"),
        # MARK: optional index for faster admin filtering; harmless if you skip
        Index("ix_users_deleted_at", "deleted_at"),
    )


# ---------- SESSIONS (game events) ----------
class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)

    starts_at: Mapped[datetime] = mapped_column(pg.TIMESTAMP(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(sa.Text, nullable=False)  # IANA name, e.g., 'America/Vancouver'

    capacity: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    fee_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False)

    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default="scheduled",
        server_default=sa.text("'scheduled'"),
    )  # 'scheduled' | 'closed' | 'canceled'

    created_at: Mapped[datetime] = mapped_column(
        pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (
        CheckConstraint("capacity > 0", name="sessions_capacity_pos"),
        CheckConstraint("fee_cents >= 0", name="sessions_fee_nonneg"),
        CheckConstraint("status in ('scheduled','closed','canceled')", name="sessions_status"),
        Index("ix_sessions_starts_at", "starts_at"),
    )


# ---------- REGISTRATIONS ----------
class Registration(Base):
    __tablename__ = "registrations"

    id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    session_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False)
    host_user_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    group_key: Mapped[uuid.UUID | None] = mapped_column(pg.UUID(as_uuid=True), nullable=True)
    is_host: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # total seats in this registration: host (1) + 0..2 guests  -> 1..3
    seats: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    guest_names: Mapped[List[str]] = mapped_column(
        pg.ARRAY(sa.Text),
        nullable=False,
        server_default=sa.text("'{}'::text[]"),
    )

    state: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default="waitlisted",
        server_default=sa.text("'waitlisted'"),
    )  # 'confirmed' | 'waitlisted' | 'canceled'
    waitlist_pos: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    canceled_at: Mapped[Optional[datetime]] = mapped_column(pg.TIMESTAMP(timezone=True), nullable=True)
    canceled_from_state: Mapped[Optional[str]] = mapped_column(
        sa.Text,
        nullable=True,
        doc="State the registration was in when it was canceled (confirmed|waitlisted)",
    )
    
    
    __table_args__ = (
        CheckConstraint("seats >= 1 AND seats <= 3", name="registrations_seats_range"),
        CheckConstraint("state in ('confirmed','waitlisted','canceled')", name="registrations_state"),
        # Partial unique index to allow re-register after cancel
        Index(
            "uq_reg_active_once",
            "session_id",
            "host_user_id",
            unique=True,
            postgresql_where=sa.text("state <> 'canceled'"),
        ),
        Index("ix_reg_session_state_pos", "session_id", "state", "waitlist_pos"),
    )


# ---------- LEDGER (append-only) ----------
class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)

    user_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(pg.UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=True)
    registration_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("registrations.id"), nullable=True
    )
    
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        sa.Text, nullable=True, unique=True
    )

    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)  # see CheckConstraint below
    amount_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False)  # + to user, - from user
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default="posted",
        server_default=sa.text("'posted'"),
    )  # 'posted' | 'void'

    created_at: Mapped[datetime] = mapped_column(
        pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (
        # MARK: changed — allow 'hold' (keep 'fee_hold' for backward compatibility)
        CheckConstraint(
            "kind in ('deposit_in','fee_hold','hold','fee_capture','hold_release','refund','penalty')",
            name="ledger_kind",
        ),
        CheckConstraint("status in ('posted','void')", name="ledger_status"),
        Index("ix_ledger_user", "user_id"),
        Index("ix_ledger_session", "session_id"),
        Index("ix_ledger_registration", "registration_id"),
    )


# ---------- WALLETS (materialized totals; logic added later) ----------
class Wallet(Base):
    __tablename__ = "wallets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    posted_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    holds_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    )

# ---------- EVENTS OUTBOX ----------
class EventsOutbox(Base):
    __tablename__ = "events_outbox"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload: Mapped[dict] = mapped_column(pg.JSONB, nullable=False)
    available_at: Mapped[datetime] = mapped_column(pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()"))
    sent_at: Mapped[Optional[datetime]] = mapped_column(pg.TIMESTAMP(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    error: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()"))


# ---------- GMAIL OAUTH TOKENS ----------
class GmailToken(Base):
    """Stores Gmail OAuth refresh tokens for watching mailbox"""
    __tablename__ = "gmail_tokens"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(pg.CITEXT, nullable=False, unique=True)  # Gmail address being watched
    refresh_token: Mapped[str] = mapped_column(sa.Text, nullable=False)  # OAuth refresh token - to renew watch
    history_id: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)  # Last processed historyId
    watch_expiration: Mapped[Optional[datetime]] = mapped_column(pg.TIMESTAMP(timezone=True), nullable=True)  # When to renew watch (~7 days)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True, server_default=sa.text("true"))
    created_at: Mapped[datetime] = mapped_column(pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()"))
    updated_at: Mapped[datetime] = mapped_column(pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()"), onupdate=sa.text("now()"))
