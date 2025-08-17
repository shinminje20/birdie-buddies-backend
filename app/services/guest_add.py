from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

import sqlalchemy as sa
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Registration, Session as SessionModel, Wallet
from ..repos import ledger as ledger_repo
from .tx import begin_serializable_tx
from .promotion import enqueue_promotion_check
from ..repos.outbox import add_outbox_event


class GuestAddError(Exception): ...
class Forbidden(GuestAddError): ...
class NotFound(GuestAddError): ...
class InvalidState(GuestAddError): ...
class LimitExceeded(GuestAddError): ...
class InsufficientFunds(GuestAddError): ...
class TooLate(GuestAddError): ...



async def _next_waitlist_pos(db, session_id):
    row = await db.execute(
        select(func.coalesce(func.max(Registration.waitlist_pos), 0))
        .where(Registration.session_id == session_id, Registration.state == "waitlisted")
    )
    return int(row.scalar_one()) + 1


async def add_guest_registration(
    db: AsyncSession,
    *,
    host_registration_id: uuid.UUID,
    guest_name: str,
    caller_user_id: uuid.UUID,
    caller_is_admin: bool,
) -> tuple[uuid.UUID, str, Optional[int]]:
    """
    Create a 1-seat guest registration tied to the host's group_key.
    Returns: (guest_registration_id, state ['confirmed'|'waitlisted'], waitlist_pos|None)
    """
    guest_name = guest_name.strip()
    if not guest_name:
        raise InvalidState("guest name required")

    await begin_serializable_tx(db)

    # Lock host reg & session
    row = await db.execute(
        select(Registration, SessionModel)
        .join(SessionModel, Registration.session_id == SessionModel.id)
        .where(Registration.id == host_registration_id)
        .with_for_update()
    )
    tup = row.first()
    if not tup:
        await db.rollback()
        raise NotFound("host registration not found")

    host_reg, sess = tup

    # Permissions: only the host of the group (or admin)
    if not caller_is_admin and host_reg.host_user_id != caller_user_id:
        await db.rollback()
        raise Forbidden()

    # Session status/time checks
    now_utc = datetime.now(timezone.utc)
    if sess.status != "scheduled":
        await db.rollback()
        raise InvalidState("session not accepting signups")
    if now_utc >= sess.starts_at:
        await db.rollback()
        raise TooLate("session already started")

    # Ensure group_key on host; if none, assign one now
    if host_reg.group_key is None:
        new_key = uuid.uuid4()
        await db.execute(
            update(Registration)
            .where(Registration.id == host_reg.id)
            .values(group_key=new_key)
        )
        host_reg.group_key = new_key
        await db.flush()

    # Enforce max 2 active guests (confirmed or waitlisted, not canceled)
    cnt_row = await db.execute(
        select(func.count()).where(
            Registration.session_id == host_reg.session_id,
            Registration.group_key == host_reg.group_key,
            Registration.id != host_reg.id,
            Registration.is_host == False,
            Registration.state != "canceled",
            )
    )
    active_guests = int(cnt_row.scalar_one())
    if active_guests >= 2:
        await db.rollback()
        raise LimitExceeded("maximum 2 guests per host")

    # Wallet sufficiency
    wrow = await db.execute(select(Wallet).where(Wallet.user_id == host_reg.host_user_id).with_for_update())
    wallet = wrow.scalar_one_or_none()
    posted = int(wallet.posted_cents) if wallet else 0
    holds = int(wallet.holds_cents) if wallet else 0
    available = posted - holds
    fee = int(sess.fee_cents)
    if available < fee:
        await db.rollback()
        raise InsufficientFunds()

    # Capacity & fairness decision
    # If any waitlist exists, force queue-at-tail
    wl_exists_row = await db.execute(
        select(func.count()).where(
            Registration.session_id == sess.id, Registration.state == "waitlisted"
        )
    )
    waitlist_exists = int(wl_exists_row.scalar_one()) > 0

    # Remaining seats
    taken_row = await db.execute(
        select(func.coalesce(func.sum(Registration.seats), 0))
        .where(Registration.session_id == sess.id, Registration.state == "confirmed")
    )
    confirmed_seats = int(taken_row.scalar_one())
    remaining = max(0, sess.capacity - confirmed_seats)

    # Create guest reg helper
    async def _mk_guest(state: str, waitlist_pos: Optional[int]) -> Registration:
        g = Registration(
            session_id=sess.id,
            host_user_id=host_reg.host_user_id,
            seats=1,
            guest_names=[guest_name],
            state=state,
            waitlist_pos=waitlist_pos,
            group_key=host_reg.group_key,
            is_host=False,
        )
        db.add(g)
        await db.flush()
        return g

    if waitlist_exists or remaining <= 0:
        # Always queue at tail
        pos = await _next_waitlist_pos(db, sess.id)
        g = await _mk_guest("waitlisted", pos)
        # Hold funds for this guest seat
        await ledger_repo.apply_ledger_entry(
            db,
            user_id=host_reg.host_user_id,
            kind="hold",
            amount_cents=fee,
            session_id=sess.id,
            registration_id=g.id,
            idempotency_key=f"hold:addguest:{g.id}",
        )
        await add_outbox_event(
            db,
            channel=f"session:{sess.id}",
            payload={"type": "registration_waitlisted", "session_id": str(sess.id), "registration_id": str(g.id), "seats": 1, "waitlist_pos": pos},
        )
        await db.commit()
        return (g.id, "waitlisted", pos)

    # No waitlist and capacity available -> confirm immediately
    g = await _mk_guest("confirmed", None)
    await ledger_repo.apply_ledger_entry(
        db,
        user_id=host_reg.host_user_id,
        kind="fee_capture",
        amount_cents=-fee,
        session_id=sess.id,
        registration_id=g.id,
        idempotency_key=f"cap:addguest:{g.id}",
    )
    await add_outbox_event(
        db,
        channel=f"session:{sess.id}",
        payload={"type": "registration_confirmed", "session_id": str(sess.id), "registration_id": str(g.id), "seats": 1},
    )
    await db.commit()
    return (g.id, "confirmed", None)
