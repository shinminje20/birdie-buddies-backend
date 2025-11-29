from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

import sqlalchemy as sa
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Session as SessionModel, Registration
from ..repos import ledger_repo as ledger_repo
from .tx import begin_serializable_tx
from .promotion import enqueue_promotion_check
from ..repos.outbox import add_outbox_event


class LifecycleError(Exception): ...
class InvalidTransition(LifecycleError): ...
class CapacityBelowConfirmed(LifecycleError): ...
class NotFound(LifecycleError): ...


async def admin_update_session(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    new_capacity: Optional[int],
    new_status: Optional[str],
) -> SessionModel:
    """
    Applies capacity/status updates with side-effects:
      - capacity cannot drop below confirmed seats
      - status transitions enforced
      - capacity increase -> enqueue promotion
      - status=canceled -> bulk cancel (refund/release) all active regs
    Returns the updated session row.
    """
    await begin_serializable_tx(db)

    # Lock session
    srow = await db.execute(
        select(SessionModel).where(SessionModel.id == session_id).with_for_update()
    )
    sess = srow.scalar_one_or_none()
    if not sess:
        await db.rollback()
        raise NotFound("session")

    # Compute confirmed seats
    taken = await db.execute(
        select(func.coalesce(func.sum(Registration.seats), 0)).where(
            Registration.session_id == session_id,
            Registration.state == "confirmed",
        )
    )
    confirmed = int(taken.scalar_one())

    # Capacity rules
    if new_capacity is not None and new_capacity < confirmed:
        await db.rollback()
        raise CapacityBelowConfirmed()

    # Status transition rules
    old_status = sess.status
    if new_status is not None and new_status != old_status:
        if old_status == "canceled":
            await db.rollback()
            raise InvalidTransition("cannot change from canceled")
        allowed = {("scheduled","closed"), ("closed","scheduled"), ("scheduled","canceled"), ("closed","canceled")}
        if (old_status, new_status) not in allowed:
            await db.rollback()
            raise InvalidTransition(f"{old_status} -> {new_status} not allowed")

    # Apply field updates
    updates = {}
    
    if new_capacity is not None:
        updates["capacity"] = new_capacity
    
    if new_status is not None:
        updates["status"] = new_status
        
    if updates:
        await db.execute(update(SessionModel).where(SessionModel.id == session_id).values(**updates))
        await db.flush()
        
        if new_status is not None and new_status != old_status:
            await add_outbox_event(
                db,
                channel=f"session:{session_id}",
                payload={
                    "type": "session_status_changed",
                    "session_id": str(session_id),
                    "old_status": old_status,
                    "new_status": new_status,
                },
            )

    # Handle side-effects

    # 1) Cancel session → refund/release & mark regs canceled (idempotent keys)
    if new_status == "canceled":
        now = datetime.now(timezone.utc)

        # Fetch all active regs
        regs = (await db.execute(
            select(Registration)
            .where(Registration.session_id == session_id, Registration.state != "canceled")
            .with_for_update()
        )).scalars().all()

        for reg in regs:
            total_fee = reg.seats * sess.fee_cents
            if reg.state == "confirmed":
                # Full refund (positive), no penalty on session cancel
                await ledger_repo.apply_ledger_entry(
                    db,
                    user_id=reg.host_user_id,
                    kind="refund",
                    amount_cents=total_fee,
                    session_id=sess.id,
                    registration_id=reg.id,
                    idempotency_key=f"refund_sess_cancel:{reg.id}",
                )
                # nothing to release: confirmed entries already released their hold earlier
            elif reg.state == "waitlisted":
                # Release the outstanding hold
                await ledger_repo.apply_ledger_entry(
                    db,
                    user_id=reg.host_user_id,
                    kind="hold_release",
                    amount_cents=-total_fee,  # decrease holds
                    session_id=sess.id,
                    registration_id=reg.id,
                    idempotency_key=f"release_sess_cancel:{reg.id}",
                )

            reg.state = "canceled"
            reg.canceled_at = now
            
        await db.flush()

        await add_outbox_event(
            db,
            channel=f"session:{session_id}",
            payload={"type": "session_canceled", "session_id": str(session_id)},
        )
        

        await db.commit()
        # No promotions when canceled
        return sess

    # 1b) Close session (non-cancel) → release waitlist holds and mark them canceled
    if new_status == "closed":
        now = datetime.now(timezone.utc)

        waitlisted = (await db.execute(
            select(Registration)
            .where(Registration.session_id == session_id, Registration.state == "waitlisted")
            .with_for_update()
        )).scalars().all()

        for reg in waitlisted:
            total_fee = reg.seats * sess.fee_cents
            await ledger_repo.apply_ledger_entry(
                db,
                user_id=reg.host_user_id,
                kind="hold_release",
                amount_cents=-total_fee,  # decrease holds
                session_id=sess.id,
                registration_id=reg.id,
                idempotency_key=f"release_close:{reg.id}",
            )
            reg.state = "canceled"
            reg.canceled_at = now
            reg.waitlist_pos = None

        await db.flush()
        await db.commit()
        return sess

    # 2) Capacity increased while still scheduled → enqueue promotion
    if new_capacity is not None and new_capacity > confirmed and (new_status or old_status) == "scheduled":
        
        if new_capacity is not None and new_capacity != sess.capacity:
            await add_outbox_event(
                db,
                channel=f"session:{session_id}",
                payload={
                    "type": "session_capacity_changed",
                    "session_id": str(session_id),
                    "capacity": new_capacity,
                },
            )

        
        await db.commit()
        await enqueue_promotion_check(session_id)
        return sess

    await add_outbox_event(
        db,
        channel=f"session:{session_id}",
        payload={"type": "session_canceled", "session_id": str(session_id)},
    )

    # Default path
    await db.commit()
    return sess
