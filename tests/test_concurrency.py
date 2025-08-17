import asyncio
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Registration, Wallet
from app.services.registration_allocator import process_registration_request
from tests.conftest import mk_user, deposit, mk_session

import pytest
pytestmark = pytest.mark.asyncio


async def _register_concurrently(session_id: uuid.UUID, user_ids: list[uuid.UUID], seats_each: int):
    async def one(u: uuid.UUID):
        async with SessionLocal() as s:
            return await process_registration_request(
                s,
                request_id=f"req:{session_id}:{u}",
                session_id=session_id,
                user_id=u,
                seats=seats_each,
                guest_names=[] if seats_each == 1 else [f"g{u.hex[:4]}"] * (seats_each - 1),
            )

    return await asyncio.gather(*[one(u) for u in user_ids], return_exceptions=True)


async def _waitlist_positions_unique(db: AsyncSession, session_id: uuid.UUID):
    rows = (
        await db.execute(
            select(Registration.waitlist_pos).where(
                Registration.session_id == session_id, Registration.state == "waitlisted"
            )
        )
    ).scalars().all()
    rows = [r for r in rows if r is not None]
    assert len(rows) == len(set(rows))
    if rows:
        assert sorted(rows) == list(range(1, len(rows) + 1))


async def _confirmed_seat_sum(db: AsyncSession, session_id: uuid.UUID) -> int:
    s = await db.execute(
        select(func.coalesce(func.sum(Registration.seats), 0)).where(
            Registration.session_id == session_id, Registration.state == "confirmed"
        )
    )
    return int(s.scalar_one())


async def _wallet_invariants(db: AsyncSession):
    rows = (await db.execute(select(Wallet))).scalars().all()
    for w in rows:
        assert w.holds_cents >= 0
        assert w.posted_cents >= 0
        assert (w.posted_cents - w.holds_cents) >= 0  # available >= 0


async def test_concurrent_registrations_respect_capacity_and_waitlist(db: AsyncSession):
    N = 18
    cap = 10
    fee = 800
    tz = "America/Vancouver"
    starts = datetime.now(timezone.utc) + timedelta(days=2)
    sid = await mk_session(db, title="cap-test", starts_at_utc=starts, tz=tz, capacity=cap, fee_cents=fee)

    users = []
    for i in range(N):
        uid = await mk_user(db, email=f"user{i}@x.test", name=f"U{i}")
        await deposit(db, uid, amount_cents=fee * 10)
        users.append(uid)

    await _register_concurrently(sid, users, seats_each=1)

    confirmed = await _confirmed_seat_sum(db, sid)
    assert confirmed == min(cap, N)
    assert confirmed <= cap

    await _waitlist_positions_unique(db, sid)
    await _wallet_invariants(db)


async def test_concurrent_group_sizes_do_not_split(db: AsyncSession):
    cap, fee = 5, 800
    tz = "America/Vancouver"
    starts = datetime.now(timezone.utc) + timedelta(days=2)
    sid = await mk_session(db, title="group-fit", starts_at_utc=starts, tz=tz, capacity=cap, fee_cents=fee)

    u_big = await mk_user(db, "big@x.test", "BIG")
    u_a = await mk_user(db, "a@x.test", "A")
    u_b = await mk_user(db, "b@x.test", "B")
    for uid in [u_big, u_a, u_b]:
        await deposit(db, uid, fee * 10)

    async def run(uid: uuid.UUID, seats: int, guests: list[str]):
        async with SessionLocal() as s:
            return await process_registration_request(
                s, request_id=f"r:{uid}:{seats}", session_id=sid, user_id=uid, seats=seats, guest_names=guests
            )

    # Run together
    await asyncio.gather(
        run(u_big, 3, ["g1", "g2"]),
        run(u_a, 1, []),
        run(u_b, 1, []),
    )

    confirmed = await _confirmed_seat_sum(db, sid)
    # Either all confirmed (5), or the 3-seater waits while two singles fill (2)
    assert confirmed in (5, 2)

    await _waitlist_positions_unique(db, sid)
    await _wallet_invariants(db)
