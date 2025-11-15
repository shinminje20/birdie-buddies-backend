# app/services/cancellation.py
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Tuple, List

import sqlalchemy as sa
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Registration, Session as SessionModel
from ..repos import ledger_repo as ledger_repo
from .promotion import enqueue_promotion_check
from ..repos.outbox import add_outbox_event
from .tx import begin_serializable_tx


class CancelResult(Tuple[int, int, str]):  # refund_cents, penalty_cents, final_state
    """Return type for cancel_registration."""
    pass


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _compute_policy(now_utc: datetime, session_starts_utc: datetime, tz_name: str, total_fee: int) -> tuple[int, int]:
    """
    Returns (refund_cents, penalty_cents). Penalty is negative.
    Rules:
      - If cancel BEFORE local 00:00 of session day -> full refund.
      - If local same day (00:00 <= now < starts_at) -> refund 50%, penalty 50%.
      - If now >= starts_at -> cancellation not allowed (caller returns too_late).
    """
    tz = ZoneInfo(tz_name)
    start_local = session_starts_utc.astimezone(tz)
    midnight_local = start_local.replace(hour=0, minute=0, second=0, microsecond=0)
    now_local = now_utc.astimezone(tz)

    if now_local < midnight_local:
        return (total_fee, 0)
    if now_local < start_local:
        # split 50/50; ensure integers sum correctly
        # refund = total_fee // 2
        penalty = -(total_fee // 2)  # negative number
        return (total_fee, penalty)
    # After start: disallow; caller will handle
    return (0, 0)


async def _collapse_waitlist_after(db: AsyncSession, session_id: uuid.UUID, vacated_pos: Optional[int]) -> None:
    """Shift waitlist positions down to keep them contiguous after removing an entry."""
    if not vacated_pos:
        return
    await db.execute(
        update(Registration)
        .where(
            Registration.session_id == session_id,
            Registration.state == "waitlisted",
            Registration.waitlist_pos > vacated_pos,
        )
        .values(waitlist_pos=Registration.waitlist_pos - 1)
    )


async def cancel_registration(
    db: AsyncSession,
    *,
    registration_id: uuid.UUID,
    caller_user_id: uuid.UUID,
    caller_is_admin: bool,
) -> CancelResult:
    """Cancel a registration.

    - Works for both confirmed and waitlisted registrations.
    - Applies refund/penalty per policy for confirmed seats; releases holds for waitlisted.
    - If the canceled registration is the HOST seat in a split group (group_key set and guest_names == []),
      cascade-cancels all guest registrations in the same group.
    - Returns (refund_cents_total, penalty_cents_total, final_state) where totals include any cascaded guest seats.
    """
    await begin_serializable_tx(db)

    res = await db.execute(
        select(Registration, SessionModel)
        .join(SessionModel, Registration.session_id == SessionModel.id)
        .where(Registration.id == registration_id)
        .with_for_update()
    )
    row = res.first()
    if not row:
        # Nothing to do
        await db.rollback()
        return (0, 0, "not_found")

    reg, sess = row

    # Permission: host or admin
    if not caller_is_admin and reg.host_user_id != caller_user_id:
        await db.rollback()
        raise PermissionError("forbidden")

    # If already canceled, return a stable result (no-op)
    if reg.state == "canceled":
        await db.rollback()
        return (0, 0, "canceled")

    # Disallow after session starts
    now_utc = _now_utc()
    if now_utc >= sess.starts_at:
        await db.rollback()
        return (0, 0, "too_late")

    # We'll aggregate totals across cascade if applicable
    refund_total = 0
    penalty_total = 0

    # Helper to cancel a single reg row (confirmed or waitlisted)
    async def _cancel_one(target: Registration) -> tuple[int, int]:
        nonlocal now_utc, sess
        fee = target.seats * sess.fee_cents

        refund_cents = 0
        penalty_cents = 0

        if target.state == "waitlisted":
            # Release the corresponding hold portion
            await ledger_repo.apply_ledger_entry(
                db,
                user_id=target.host_user_id,
                kind="hold_release",
                amount_cents=-fee,  # decrease holds
                session_id=sess.id,
                registration_id=target.id,
                idempotency_key=f"rel_cancel:{target.id}",
            )
            # Remove from waitlist and collapse
            old_pos = target.waitlist_pos
            target.state = "canceled"
            target.canceled_at = now_utc
            target.waitlist_pos = None
            await db.flush()
            await _collapse_waitlist_after(db, sess.id, old_pos)

        elif target.state == "confirmed":
            # Compute policy
            refund_cents, penalty_cents = _compute_policy(now_utc, sess.starts_at, sess.timezone, fee)

            # Apply refund/penalty
            if refund_cents:
                await ledger_repo.apply_ledger_entry(
                    db,
                    user_id=target.host_user_id,
                    kind="refund",
                    amount_cents=refund_cents,   # increase posted by refund
                    session_id=sess.id,
                    registration_id=target.id,
                    idempotency_key=f"refund_cancel:{target.id}",
                )
            if penalty_cents:
                await ledger_repo.apply_ledger_entry(
                    db,
                    user_id=target.host_user_id,
                    kind="penalty",
                    amount_cents=penalty_cents,  # negative
                    session_id=sess.id,
                    registration_id=target.id,
                    idempotency_key=f"penalty_cancel:{target.id}",
                )

            target.state = "canceled"
            target.canceled_at = now_utc
            await db.flush()

        # Outbox event per canceled reg
        await add_outbox_event(
            db,
            channel=f"session:{sess.id}",
            payload={
                "type": "registration_canceled",
                "session_id": str(sess.id),
                "registration_id": str(target.id),
                "host_user_id": str(target.host_user_id),
                "seats": target.seats,
                "ts": now_utc.isoformat(),
            },
        )

        return (refund_cents, penalty_cents)

    # Determine if this is the host seat in a split group (seats=1 and no guest_names)
    is_host_seat_in_group = bool(reg.group_key) and reg.seats == 1 and not (reg.guest_names or [])

    # Cancel the requested registration first
    r_ref, r_pen = await _cancel_one(reg)
    refund_total += r_ref
    penalty_total += r_pen

    # Cascade if host seat in group: cancel all sibling guest regs (state != canceled)
    if is_host_seat_in_group:
        sib_rows = await db.execute(
            select(Registration)
            .where(
                Registration.session_id == reg.session_id,
                Registration.group_key == reg.group_key,
                Registration.id != reg.id,
                Registration.state != "canceled",
            )
            .with_for_update(skip_locked=True)
        )
        siblings = list(sib_rows.scalars().all())

        for sreg in siblings:
            s_ref, s_pen = await _cancel_one(sreg)
            refund_total += s_ref
            penalty_total += s_pen

    await db.commit()

    # Trigger promotion check (capacity may have freed)
    await enqueue_promotion_check(sess.id)

    return (refund_total, penalty_total, "canceled")
