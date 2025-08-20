from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.services.registration_allocator import process_registration_request
from app.services import cancellation as cancel_service
from app.services.cancellation import cancel_registration
from tests.conftest import mk_user, deposit, mk_session

import pytest
pytestmark = pytest.mark.asyncio


def _dt_local(tz: str, y, mo, d, h, m=0):
    return datetime(y, mo, d, h, m, tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


async def _mk_confirmed(sid, uid, seats=1):
    async with SessionLocal() as s:
        state, reg_id, _ = await process_registration_request(
            s, request_id=f"r:{uid}", session_id=sid, user_id=uid, seats=seats, guest_names=[]
        )
    return reg_id


async def test_cancellation_before_midnight_full_refund(db: AsyncSession, monkeypatch):
    tz = "America/Vancouver"
    starts_at_utc = _dt_local(tz, 2026, 1, 15, 20)
    sid = await mk_session(db, title="cancel-full", starts_at_utc=starts_at_utc, tz=tz, capacity=4, fee_cents=1000)

    uid = await mk_user(db, "cf@x.test", "CF")
    await deposit(db, uid, 10_000)
    reg_id = await _mk_confirmed(sid, uid)

    fixed_now = _dt_local(tz, 2026, 1, 14, 12)  # day before (local)
    monkeypatch.setattr(cancel_service, "_now_utc", lambda: fixed_now)

    async with SessionLocal() as s:
        refund, penalty, state = await cancel_registration(
            s, registration_id=reg_id, caller_user_id=uid, caller_is_admin=False
        )
    assert state == "canceled"
    assert refund == 1000 and penalty == 0


async def test_cancellation_same_day_half_penalty(db: AsyncSession, monkeypatch):
    tz = "America/Vancouver"
    fee = 900
    starts_at_utc = _dt_local(tz, 2026, 2, 10, 20)
    sid = await mk_session(db, title="cancel-half", starts_at_utc=starts_at_utc, tz=tz, capacity=4, fee_cents=fee)

    uid = await mk_user(db, "ch@x.test", "CH")
    await deposit(db, uid, 10_000)
    reg_id = await _mk_confirmed(sid, uid)

    fixed_now = _dt_local(tz, 2026, 2, 10, 9)  # same day, before start
    monkeypatch.setattr(cancel_service, "_now_utc", lambda: fixed_now)

    async with SessionLocal() as s:
        refund, penalty, state = await cancel_registration(
            s, registration_id=reg_id, caller_user_id=uid, caller_is_admin=False
        )
    assert state == "canceled"
    assert refund == fee / 2 and penalty == -(fee - fee / 2)


async def test_cancellation_after_start_disallowed(db: AsyncSession, monkeypatch):
    tz = "America/Vancouver"
    fee = 800
    starts_at_utc = _dt_local(tz, 2026, 3, 5, 20)
    sid = await mk_session(db, title="cancel-too-late", starts_at_utc=starts_at_utc, tz=tz, capacity=4, fee_cents=fee)

    uid = await mk_user(db, "ctl@x.test", "CTL")
    await deposit(db, uid, 10_000)
    reg_id = await _mk_confirmed(sid, uid)

    fixed_now = starts_at_utc + timedelta(hours=1)  # after start (UTC)
    monkeypatch.setattr(cancel_service, "_now_utc", lambda: fixed_now)

    async with SessionLocal() as s:
        refund, penalty, state = await cancel_registration(
            s, registration_id=reg_id, caller_user_id=uid, caller_is_admin=False
        )
    assert state == "too_late"
    assert refund == 0 and penalty == 0
