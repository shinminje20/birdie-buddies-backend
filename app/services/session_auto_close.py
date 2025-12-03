from __future__ import annotations
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Session as SessionModel
from ..models import Registration
from ..repos import ledger_repo
from ..repos.outbox import add_outbox_event
from .tx import begin_serializable_tx

from ..observability.metrics import SESSIONS_AUTOCLOSED

async def close_due_sessions(db: AsyncSession, *, batch: int = 200) -> list[str]:
    """
    Close at most `batch` sessions whose starts_at are at least 2 hours in the past
    (starts_at + 2h <= now) and status == 'scheduled'. Returns list of session_id
    strings closed in this run.
    """
    now = datetime.now(timezone.utc)
    close_cutoff = now - timedelta(hours=2)
    closed: list[str] = []

    # Start a fresh tx; SELECT ... FOR UPDATE SKIP LOCKED to avoid races with admins
    await begin_serializable_tx(db)

    rows = await db.execute(
        select(SessionModel)
        .where(SessionModel.status == "scheduled", SessionModel.starts_at <= close_cutoff)
        .order_by(SessionModel.starts_at.asc())
        .limit(batch)
        .with_for_update(skip_locked=True)
    )
    sessions = list(rows.scalars().all())
    if not sessions:
        await db.rollback()
        return closed

    for s in sessions:
        # Transition allowed by our lifecycle rules (scheduled -> closed)
        s.status = "closed"
        SESSIONS_AUTOCLOSED.inc()
        closed.append(str(s.id))

        # Refund/release any waitlisted holds; waitlists are moot once closed.
        waitlisted = (
            await db.execute(
                select(Registration)
                .where(Registration.session_id == s.id, Registration.state == "waitlisted")
                .with_for_update()
            )
        ).scalars().all()

        for reg in waitlisted:
            hold_cents = reg.seats * s.fee_cents
            await ledger_repo.apply_ledger_entry(
                db,
                user_id=reg.host_user_id,
                kind="hold_release",
                amount_cents=-hold_cents,  # decrease holds
                session_id=s.id,
                registration_id=reg.id,
                idempotency_key=f"release_auto_close:{reg.id}",
            )
            reg.state = "canceled"
            reg.canceled_at = now
            reg.waitlist_pos = None

        # Outbox: notify listeners
        await add_outbox_event(
            db,
            channel=f"session:{s.id}",
            payload={"type": "session_status_changed", "session_id": str(s.id), "old_status": "scheduled", "new_status": "closed"},
        )

    await db.flush()
    await db.commit()
    return closed
