from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence, Tuple

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update

from ..models import Session, Registration


def _confirmed_seats_scalar(session_id_col) -> sa.sql.elements.ColumnElement[int]:
    # SUM of confirmed seats for a session (scalar subquery)
    return (
        select(func.coalesce(func.sum(Registration.seats), 0))
        .where(Registration.session_id == session_id_col, Registration.state == "confirmed")
        .scalar_subquery()
    )


async def create_session(
    db: AsyncSession,
    *,
    title: Optional[str],
    starts_at_utc: datetime,
    timezone_name: str,
    capacity: int,
    fee_cents: float,
) -> Session:
    s = Session(
        title=title,
        starts_at=starts_at_utc,
        timezone=timezone_name,
        capacity=capacity,
        fee_cents=fee_cents,
        status="scheduled",
    )
    db.add(s)
    await db.flush()
    return s


async def list_upcoming(
    db: AsyncSession,
    *,
    now_utc: datetime,
    limit: int = 50,
) -> Sequence[Tuple[Session, int]]:
    # Return (Session, confirmed_seats) for scheduled sessions in the future
    csum = _confirmed_seats_scalar(Session.id).label("confirmed_seats")
    q = (
        select(Session, csum)
        .where(Session.status == "scheduled", Session.starts_at >= now_utc)
        .order_by(Session.starts_at.asc())
        .limit(limit)
    )
    res = await db.execute(q)
    return list(res.all())


async def get_with_counts(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
) -> Optional[Tuple[Session, int]]:
    csum = _confirmed_seats_scalar(Session.id).label("confirmed_seats")
    q = select(Session, csum).where(Session.id == session_id)
    res = await db.execute(q)
    row = res.first()
    return row if row else None


async def update_session(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    capacity: Optional[int] = None,
    status: Optional[str] = None,
) -> Optional[Session]:
    # Load current session + confirmed count to validate capacity change
    row = await get_with_counts(db, session_id=session_id)
    if not row:
        return None
    sess, confirmed = row

    if capacity is not None and capacity < confirmed:
        # Not allowed to set capacity below already-confirmed seats
        raise ValueError("capacity_below_confirmed")

    values = {}
    if capacity is not None:
        values["capacity"] = capacity
    if status is not None:
        values["status"] = status

    if not values:
        return sess

    await db.execute(
        update(Session).where(Session.id == session_id).values(**values)
    )
    await db.flush()
    # Re-read
    row2 = await get_with_counts(db, session_id=session_id)
    return row2[0] if row2 else None
