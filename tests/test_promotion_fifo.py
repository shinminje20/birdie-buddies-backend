import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Registration
from app.services.registration_allocator import process_registration_request
from app.services.cancellation import cancel_registration
from app.services.waitlist_promotion import promote_waitlist_fifo
from tests.conftest import mk_user, deposit, mk_session

import pytest
pytestmark = pytest.mark.asyncio


async def _status(db: AsyncSession, reg_id: uuid.UUID) -> tuple[str, int | None]:
    row = (await db.execute(select(Registration).where(Registration.id == reg_id))).scalar_one()
    return row.state, row.waitlist_pos


async def test_waitlist_promotion_strict_fifo(db: AsyncSession):
    fee = 800
    tz = "America/Vancouver"
    starts = datetime.now(timezone.utc) + timedelta(days=2)
    sid = await mk_session(db, title="fifo", starts_at_utc=starts, tz=tz, capacity=3, fee_cents=fee)

    # 3 confirmed
    cids = []
    for i in range(3):
        uid = await mk_user(db, f"c{i}@x.test", f"C{i}")
        await deposit(db, uid, fee * 10)
        async with SessionLocal() as s:
            await process_registration_request(s, request_id=f"r:{uid}", session_id=sid, user_id=uid, seats=1, guest_names=[])
        cids.append(uid)

    # Waitlist head (needs 2) and tail (needs 1)
    uh = await mk_user(db, "head@x.test", "HEAD")
    ut = await mk_user(db, "tail@x.test", "TAIL")
    for u in [uh, ut]:
        await deposit(db, u, fee * 10)

    async with SessionLocal() as s4:
        res_h = await process_registration_request(s4, request_id="r-head", session_id=sid, user_id=uh, seats=2, guest_names=["g1"])
    async with SessionLocal() as s5:
        res_t = await process_registration_request(s5, request_id="r-tail", session_id=sid, user_id=ut, seats=1, guest_names=[])

    _, head_id, head_pos = res_h
    _, tail_id, tail_pos = res_t
    assert head_pos == 1 and tail_pos == 2  # initial waitlist order

    # Free one seat -> head (needs 2) should NOT be skipped
    async with SessionLocal() as s:
        regs = (
            await s.execute(
                select(Registration).where(Registration.session_id == sid, Registration.state == "confirmed")
            )
        ).scalars().all()
        await cancel_registration(s, registration_id=regs[0].id, caller_user_id=regs[0].host_user_id, caller_is_admin=True)

    async with SessionLocal() as s:
        promoted = await promote_waitlist_fifo(s, session_id=sid)
    assert promoted == []  # strict FIFO

    # Free another seat -> now head fits; promote it
    async with SessionLocal() as s:
        regs = (
            await s.execute(
                select(Registration).where(Registration.session_id == sid, Registration.state == "confirmed")
            )
        ).scalars().all()
        await cancel_registration(s, registration_id=regs[0].id, caller_user_id=regs[0].host_user_id, caller_is_admin=True)

    async with SessionLocal() as s:
        promoted = await promote_waitlist_fifo(s, session_id=sid)
    assert any(rid == head_id for rid, _ in promoted)

    # Positions collapse: tail becomes new head (pos=1) and remains waitlisted
    async with SessionLocal() as s:
        st_h, pos_h = await _status(s, head_id)
        st_t, pos_t = await _status(s, tail_id)
    assert st_h == "confirmed" and pos_h is None
    assert st_t == "waitlisted" and pos_t == 1
