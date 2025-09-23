from __future__ import annotations
import uuid
from typing import List, Tuple

import sqlalchemy as sa
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Session as SessionModel, Registration
from ..repos import ledger_repo as ledger_repo
from .tx import begin_serializable_tx
from ..repos.outbox import add_outbox_event
from datetime import datetime, timezone

# Strict FIFO: do not skip head if it doesn't fit
# Returns list of (registration_id, seats) that were promoted
async def promote_waitlist_fifo(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
) -> list[tuple[uuid.UUID, int]]:
    await begin_serializable_tx(db)

    # Lock session and compute remaining seats
    srow = await db.execute(
        select(SessionModel).where(SessionModel.id == session_id).with_for_update()
    )
    sess = srow.scalar_one_or_none()
    if not sess or sess.status != "scheduled":
        await db.rollback()
        return []

    taken = await db.execute(
        select(func.coalesce(func.sum(Registration.seats), 0)).where(
            Registration.session_id == session_id,
            Registration.state == "confirmed",
        )
    )
    remaining = int(taken.scalar_one())
    remaining = max(0, sess.capacity - remaining)

    if remaining <= 0:
        await db.rollback()
        return []

    promoted: list[tuple[uuid.UUID, int]] = []

    # Loop: pick head of waitlist (lowest waitlist_pos) each iteration
    while remaining > 0:
        head_row = await db.execute(
            select(Registration)
            .where(
                Registration.session_id == session_id,
                Registration.state == "waitlisted",
            )
            .order_by(Registration.waitlist_pos.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        head = head_row.scalar_one_or_none()
        if not head:
            break

        if head.seats > remaining:
            # strict FIFO: stop if head doesn't fit
            break

        total_fee = head.seats * sess.fee_cents

        # Convert hold -> capture + release
        await ledger_repo.apply_ledger_entry(
            db,
            user_id=head.host_user_id,
            kind="fee_capture",
            amount_cents=-total_fee,
            session_id=sess.id,
            registration_id=head.id,
            idempotency_key=f"cap:{head.id}",  # same keys as initial confirm path
        )
        await ledger_repo.apply_ledger_entry(
            db,
            user_id=head.host_user_id,
            kind="hold_release",
            amount_cents=-total_fee,
            session_id=sess.id,
            registration_id=head.id,
            idempotency_key=f"rel:{head.id}",
        )

        # head.state = "confirmed"
        # head.waitlist_pos = None
        # await db.flush()

        # promoted.append((head.id, head.seats))
        # remaining -= head.seats
                # capture old position BEFORE clearing it
        old_pos = head.waitlist_pos

        head.state = "confirmed"
        head.waitlist_pos = None
        await db.flush()

        # collapse positions > old_pos so they remain contiguous (1..N)
        if old_pos is not None:
            await db.execute(
                update(Registration)
                .where(
                    Registration.session_id == session_id,
                    Registration.state == "waitlisted",
                    Registration.waitlist_pos > old_pos,
                )
                .values(waitlist_pos=Registration.waitlist_pos - 1)
            )

        promoted.append((head.id, head.seats))
        remaining -= head.seats


    # waitlist_promotion.py: publish one outbox event per promoted registration
    # Right now you emit a single registration_promoted event using the last head after the loop, 
    # so multiple promotions only publish one event. 
    # Emit for each (reg_id, seats) in promoted before commit.
    if promoted:
        # publish one outbox event per promoted registration (inside the tx)
        for reg_id, seats_prom in promoted:
            await add_outbox_event(
                db,
                channel=f"session:{session_id}",
                payload={
                    "type": "registration_promoted",
                    "session_id": str(session_id),
                    "registration_id": str(reg_id),
                    # you can fetch host_user_id if needed, but it's not required for the UI refresh
                    "seats": seats_prom,
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
            )
        await db.commit()
    else:
        await db.rollback()
    return promoted
