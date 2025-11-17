from datetime import datetime, timezone, timedelta

import pytest

from app.models import Session as SessionModel
from app.repos import session_repo as sess_repo
from app.services.session_auto_close import close_due_sessions
from tests.conftest import mk_session

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
