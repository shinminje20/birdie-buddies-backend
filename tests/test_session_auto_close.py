from datetime import datetime, timezone, timedelta

import pytest

from app.models import Session as SessionModel
from app.models import Registration
from app.repos import session_repo as sess_repo
from app.repos.wallets import get_wallet_summary
from app.services.registration_allocator import process_registration_request
from app.services.session_auto_close import close_due_sessions
from app.services.session_lifecycle import admin_update_session
from tests.conftest import mk_session, mk_user, deposit
from app.db import SessionLocal

pytestmark = pytest.mark.asyncio


async def test_auto_close_waits_three_hours(db):
    now = datetime.now(timezone.utc)
    recent_sid = await mk_session(
        db,
        title="recent",
        starts_at_utc=now - timedelta(hours=2),
        tz="UTC",
        capacity=5,
        fee_cents=1000,
    )
    overdue_sid = await mk_session(
        db,
        title="overdue",
        starts_at_utc=now - timedelta(hours=4),
        tz="UTC",
        capacity=5,
        fee_cents=1000,
    )

    closed = await close_due_sessions(db, batch=10)

    assert str(overdue_sid) in closed
    assert str(recent_sid) not in closed

    overdue = await db.get(SessionModel, overdue_sid)
    recent = await db.get(SessionModel, recent_sid)
    assert overdue.status == "closed"
    assert recent.status == "scheduled"


async def test_admin_history_lists_recently_closed(db):
    now = datetime.now(timezone.utc)
    old_sid = await mk_session(
        db,
        title="old",
        starts_at_utc=now - timedelta(hours=6),
        tz="UTC",
        capacity=5,
        fee_cents=1000,
    )
    newer_sid = await mk_session(
        db,
        title="newer",
        starts_at_utc=now - timedelta(hours=4),
        tz="UTC",
        capacity=5,
        fee_cents=1000,
    )

    for sid in (old_sid, newer_sid):
        sess = await db.get(SessionModel, sid)
        sess.status = "closed"
    await db.commit()

    rows = await sess_repo.list_closed(db, now_utc=now, limit=10)
    ids = [s.id for (s, _confirmed) in rows]

    assert ids == [newer_sid, old_sid]


async def test_auto_close_releases_waitlist_holds(db):
    now = datetime.now(timezone.utc)
    fee = 1500
    # Starts in the future so we can create a waitlist entry, then shift it to the past for closure.
    sid = await mk_session(
        db,
        title="waitlist-close",
        starts_at_utc=now + timedelta(hours=2),
        tz="UTC",
        capacity=0,  # force pure waitlist
        fee_cents=fee,
    )

    uid = await mk_user(db, "wl@x.test", "Waitlist")
    await deposit(db, uid, 10_000)

    # Add a waitlisted registration
    async with SessionLocal() as s:
        state, reg_id, pos = await process_registration_request(
            s, request_id="req-1", session_id=sid, user_id=uid, seats=1, guest_names=[]
        )
        assert state == "waitlisted" and reg_id is not None and pos == 1

        # Move session start into the past so it qualifies for auto-close
        sess = await s.get(SessionModel, sid)
        sess.starts_at = now - timedelta(hours=4)
        await s.commit()

    # Holds are in place before auto-close
    async with SessionLocal() as s:
        w = await get_wallet_summary(s, uid)
        assert w.holds_cents == fee

    # Auto-close should release holds and cancel waitlisted rows
    async with SessionLocal() as s:
        closed = await close_due_sessions(s, batch=10)
        assert str(sid) in closed

    async with SessionLocal() as s:
        w = await get_wallet_summary(s, uid)
        assert w.holds_cents == 0

        reg = await s.get(Registration, reg_id)
        assert reg.state == "canceled"


async def test_admin_close_releases_waitlist_holds(db):
    now = datetime.now(timezone.utc)
    fee = 2000
    sid = await mk_session(
        db,
        title="waitlist-admin-close",
        starts_at_utc=now + timedelta(hours=1),
        tz="UTC",
        capacity=0,
        fee_cents=fee,
    )

    uid = await mk_user(db, "wl2@x.test", "Waitlist2")
    await deposit(db, uid, 10_000)

    state, reg_id, pos = await process_registration_request(
        db, request_id="req-2", session_id=sid, user_id=uid, seats=1, guest_names=[]
    )
    assert state == "waitlisted" and reg_id is not None and pos == 1

    w_before = await get_wallet_summary(db, uid)
    assert w_before.holds_cents == fee

    await admin_update_session(db, session_id=sid, new_capacity=None, new_status="closed")

    w_after = await get_wallet_summary(db, uid)
    assert w_after.holds_cents == 0

    reg = await db.get(Registration, reg_id)
    assert reg.state == "canceled"
    assert reg.waitlist_pos is None
