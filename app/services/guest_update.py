from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Tuple, List

import sqlalchemy as sa
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Registration, Session as SessionModel
from ..repos import ledger_repo as ledger_repo
from .tx import begin_serializable_tx
from .promotion import enqueue_promotion_check
from .cancellation import _compute_policy  # reuse same policy logic


class GuestUpdateError(Exception): ...
class Forbidden(GuestUpdateError): ...
class NotFound(GuestUpdateError): ...
class InvalidChange(GuestUpdateError): ...
class TooLate(GuestUpdateError): ...


async def update_guest_list(
    db: AsyncSession,
    *,
    registration_id: uuid.UUID,
    caller_user_id: uuid.UUID,
    caller_is_admin: bool,
    new_guest_names: List[str],
) -> Tuple[int, int, int, int, str]:
    """
    Replace the guest_names list for a registration.

    Returns: (old_seats, new_seats, refund_cents, penalty_cents, final_state)

    Rules:
      - Only host or admin may update.
      - Only 'confirmed' or 'waitlisted' states may be updated.
      - 'canceled' or session already started -> not allowed.
      - You can edit names or REMOVE guests (i.e., seats must not increase here).
    """

    # normalize payload
    new_guest_names = [x.strip() for x in new_guest_names if x.strip()][:2]
    target_seats = 1 + len(new_guest_names)

    await begin_serializable_tx(db)

    # lock registration + session
    row = await db.execute(
        select(Registration, SessionModel)
        .join(SessionModel, Registration.session_id == SessionModel.id)
        .where(Registration.id == registration_id)
        .with_for_update()
    )
    tup = row.first()
    if not tup:
        await db.rollback()
        raise NotFound("registration")
    reg, sess = tup

    # permission
    if not caller_is_admin and reg.host_user_id != caller_user_id:
        await db.rollback()
        raise Forbidden()

    # state/time checks
    now_utc = datetime.now(timezone.utc)
    if now_utc >= sess.starts_at:
        await db.rollback()
        raise TooLate("session_started")
    if reg.state not in ("confirmed", "waitlisted"):
        await db.rollback()
        raise InvalidChange(f"cannot modify registration in state={reg.state}")

    old_seats = reg.seats
    if target_seats > old_seats:
        await db.rollback()
        raise InvalidChange("cannot increase seats in guest edit; use registration endpoint")

    # names-only update (no seat change)
    if target_seats == old_seats:
        reg.guest_names = new_guest_names
        await db.flush()
        await db.commit()
        return (old_seats, target_seats, 0, 0, reg.state)

    # seat reduction
    remove_count = old_seats - target_seats
    per_seat_fee = sess.fee_cents
    total_delta = remove_count * per_seat_fee

    refund_cents = 0
    penalty_cents = 0

    if reg.state == "waitlisted":
        # release hold only
        await ledger_repo.apply_ledger_entry(
            db,
            user_id=reg.host_user_id,
            kind="hold_release",
            amount_cents=-total_delta,  # decrease holds
            session_id=sess.id,
            registration_id=reg.id,
            idempotency_key=f"gu_release:{reg.id}:{target_seats}",
        )
    else:
        # confirmed: policy per removed seats
        refund_cents, penalty_cents = _compute_policy(now_utc, sess.starts_at, sess.timezone, total_delta)
        if refund_cents:
            await ledger_repo.apply_ledger_entry(
                db,
                user_id=reg.host_user_id,
                kind="refund",
                amount_cents=refund_cents,
                session_id=sess.id,
                registration_id=reg.id,
                idempotency_key=f"gu_refund:{reg.id}:{target_seats}",
            )
        if penalty_cents:
            await ledger_repo.apply_ledger_entry(
                db,
                user_id=reg.host_user_id,
                kind="penalty",
                amount_cents=penalty_cents,  # negative number
                session_id=sess.id,
                registration_id=reg.id,
                idempotency_key=f"gu_penalty:{reg.id}:{target_seats}",
            )

    # apply new state
    reg.seats = target_seats
    reg.guest_names = new_guest_names
    await db.flush()
    await db.commit()

    # if confirmed shrank, free seats may enable promotions
    if reg.state == "confirmed" and remove_count > 0:
        await enqueue_promotion_check(sess.id)

    return (old_seats, target_seats, refund_cents, penalty_cents, reg.state)
