# app/services/registration_allocator.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Sequence, Tuple, Optional

import sqlalchemy as sa
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Session as SessionModel, Registration
from ..repos import ledger_repo as ledger_repo
from ..repos.outbox import add_outbox_event
from .tx import begin_serializable_tx
from ..repos.wallets import get_wallet_summary


async def _get_remaining_seats(db: AsyncSession, session_id: uuid.UUID) -> int:
    """Capacity - sum(confirmed seats)"""
    total_confirmed = await db.execute(
        select(func.coalesce(func.sum(Registration.seats), 0)).where(
            Registration.session_id == session_id,
            Registration.state == "confirmed",
        )
    )
    confirmed = int(total_confirmed.scalar_one())
    cap_row = await db.execute(select(SessionModel.capacity).where(SessionModel.id == session_id))
    capacity = int(cap_row.scalar_one())
    return max(0, capacity - confirmed)


async def _next_waitlist_pos(db: AsyncSession, session_id: uuid.UUID) -> int:
    """Compute the next tail position (no FOR UPDATE on aggregates)."""
    max_pos = await db.execute(
        select(func.coalesce(func.max(Registration.waitlist_pos), 0)).where(
            Registration.session_id == session_id,
            Registration.state == "waitlisted",
        )
    )
    return int(max_pos.scalar_one()) + 1


async def process_registration_request(
    db: AsyncSession,
    *,
    request_id: str,           # kept for compatibility (idempotency handled by the queue/worker above this service)
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    seats: int,                # 1..3 (API should validate)
    guest_names: Sequence[str] | None,
) -> Tuple[str, Optional[uuid.UUID], Optional[int], list[uuid.UUID]]:
    """
    Allocate registration(s) according to the priority rule:
      - host seat has priority; guests are handled individually.
      - if partial fit, confirm host only, guests go to waitlist tail as 1-seat regs each.

    Returns: (state_for_host, host_registration_id, waitlist_pos_for_host_if_waitlisted, all_created_registration_ids)
             state_for_host is one of {'confirmed','waitlisted','rejected'}

    This function runs inside a SERIALIZABLE transaction (see begin_serializable_tx).
    """
    await begin_serializable_tx(db)

    # 1) Lock session row (ensure status/capacity are consistent for this txn)
    srow = await db.execute(select(SessionModel).where(SessionModel.id == session_id).with_for_update())
    sess = srow.scalar_one_or_none()
    if not sess or sess.status != "scheduled":
        await db.rollback()
        return ("rejected", None, None, [])

    # (Optional) disallow after start time
    now_utc = datetime.now(timezone.utc)
    if now_utc >= sess.starts_at:
        await db.rollback()
        return ("rejected", None, None, [])

    # 2) Enforce "one active host seat per user per session"
    already_host = await db.execute(
        select(Registration.id).where(
            Registration.session_id == session_id,
            Registration.host_user_id == user_id,
            Registration.is_host.is_(True),
            Registration.state != "canceled",
        )
    )
    if already_host.first():
        await db.rollback()
        return ("rejected", None, None, [])

    # 3) Normalize guest names and seat count
    # gnames = [g.strip() for g in (guest_names or []) if g and g.strip()]
    # # keep at most 2 guests overall; clamp to requested seats - 1
    # gnames = gnames[: min(2, max(0, seats - 1))]
    # total_seats = 1 + len(gnames)  # host + guests

    # MARK: normalize guests solely from input names, cap to 2; ignore client 'seats' for logic
    gnames = [g.strip() for g in (guest_names or []) if g and g.strip()]
    gnames = gnames[:2]  # at most 2 guests policy
    total_seats = 1 + len(gnames)
    
    # MARK: optional: enforce client invariants (won't affect logic)
    if seats != total_seats:
        # don’t fail; just log or attach to your request log
        # print(f"[alloc] seats({seats}) != 1+len(guest_names)({total_seats}); using server-derived total")
        seats = total_seats
    
    # MARK: added — affordability guard (must be able to cover ALL requested seats)
    fee = int(sess.fee_cents)
    required_cents = fee * total_seats
    w = await get_wallet_summary(db, user_id)
    if w.available_cents < required_cents:
        # Not enough balance to hold/capture total seats — reject cleanly
        await db.rollback()
        return ("rejected", None, None, [])
    
    # 4) Remaining seats snapshot
    remaining = await _get_remaining_seats(db, session_id)

    # 5) Group key (used to tie host+guests together; also useful if host is waitlisted solo)
    group_key: uuid.UUID | None = uuid.uuid4() if (total_seats > 1 or remaining == 0) else None

    created_reg_ids: list[uuid.UUID] = []

    async def _create_reg(
        *, is_host: bool, state: str, seats: int, guest_names: list[str], waitlist_pos: Optional[int]
    ) -> Registration:
        r = Registration(
            session_id=session_id,
            host_user_id=user_id,
            group_key=group_key,
            is_host=is_host,
            seats=seats,
            guest_names=guest_names or [],
            state=state,
            waitlist_pos=waitlist_pos,
        )
        db.add(r)
        await db.flush()
        created_reg_ids.append(r.id)
        return r

    fee = int(sess.fee_cents)

    # ---------------------------
    # CASE A: All fit (confirm all as a single group row)
    # ---------------------------
    if remaining >= total_seats:
        # Confirm host as 1-seat row
        host_reg = await _create_reg(is_host=True, state="confirmed", seats=1, guest_names=[], waitlist_pos=None)
        await ledger_repo.apply_ledger_entry(
            db,
            user_id=user_id,
            kind="fee_capture",
            amount_cents=-fee,
            session_id=session_id,
            registration_id=host_reg.id,
            idempotency_key=f"cap:{host_reg.id}",
        )
        try:
            await add_outbox_event(
                db,
                channel=f"session:{session_id}",
                payload={"type": "registration_confirmed", "session_id": str(session_id), "registration_id": str(host_reg.id), "seats": 1},
            )
        except Exception:
            pass

        # Confirm each guest as its own 1-seat row
        for name in gnames:
            g_reg = await _create_reg(is_host=False, state="confirmed", seats=1, guest_names=[name], waitlist_pos=None)
            await ledger_repo.apply_ledger_entry(
                db,
                user_id=user_id,
                kind="fee_capture",
                amount_cents=-fee,
                session_id=session_id,
                registration_id=g_reg.id,
                idempotency_key=f"cap:{g_reg.id}",
            )
            try:
                await add_outbox_event(
                    db,
                    channel=f"session:{session_id}",
                    payload={"type": "registration_confirmed", "session_id": str(session_id), "registration_id": str(g_reg.id), "seats": 1},
                )
            except Exception:
                pass

        await db.commit()
        return ("confirmed", host_reg.id, None, created_reg_ids)

    # ---------------------------
    # CASE B: No seats left (pure waitlist). Host first, then each guest as 1-seat rows.
    # ---------------------------
    if remaining == 0:
        pos = await _next_waitlist_pos(db, session_id)
        host_reg = await _create_reg(is_host=True, state="waitlisted", seats=1, guest_names=[], waitlist_pos=pos)
        # hold funds for host seat
        await ledger_repo.apply_ledger_entry(
            db,
            user_id=user_id,
            kind="hold",
            amount_cents=fee,
            session_id=session_id,
            registration_id=host_reg.id,
            idempotency_key=f"hold:{host_reg.id}",
        )
        try:
            await add_outbox_event(
                db,
                channel=f"session:{session_id}",
                payload={"type": "registration_waitlisted", "session_id": str(session_id), "registration_id": str(host_reg.id), "seats": 1, "waitlist_pos": pos},
            )
        except Exception:
            pass

        # guests individually at the tail, preserving FIFO
        for name in gnames:
            pos += 1
            g_reg = await _create_reg(is_host=False, state="waitlisted", seats=1, guest_names=[name], waitlist_pos=pos)
            await ledger_repo.apply_ledger_entry(
                db,
                user_id=user_id,
                kind="hold",
                amount_cents=fee,
                session_id=session_id,
                registration_id=g_reg.id,
                idempotency_key=f"hold:{g_reg.id}",
            )
            try:
                await add_outbox_event(
                    db,
                    channel=f"session:{session_id}",
                    payload={
                        "type": "registration_waitlisted",
                        "session_id": str(session_id),
                        "registration_id": str(g_reg.id),
                        "seats": 1,
                        "waitlist_pos": pos,
                    },
                )
            except Exception:
                pass

        await db.commit()
        return ("waitlisted", host_reg.id, host_reg.waitlist_pos, created_reg_ids)

    # ---------------------------
    # CASE C: Partial fit (0 < remaining < total_seats)
    # Host priority, then confirm as many guests as will fit; waitlist the rest.
    # ---------------------------
    # MARK: changed — previously we confirmed ONLY the host and waitlisted all guests.
    # Now we confirm host + up to (remaining - 1) guests, each as 1-seat regs.
    host_reg = await _create_reg(is_host=True, state="confirmed", seats=1, guest_names=[], waitlist_pos=None)
    await ledger_repo.apply_ledger_entry(
        db,
        user_id=user_id,
        kind="fee_capture",
        amount_cents=-fee,
        session_id=session_id,
        registration_id=host_reg.id,
        idempotency_key=f"cap:{host_reg.id}",
    )
    try:
        await add_outbox_event(
            db,
            channel=f"session:{session_id}",
            payload={"type": "registration_confirmed", "session_id": str(session_id), "registration_id": str(host_reg.id), "seats": 1},
        )
    except Exception:
        pass

    # Seats left after host confirmation
    left = max(0, remaining - 1)

    # Confirm as many guests as will fit (FIFO within this request)
    confirmed_count = 0
    for name in gnames:
        if left <= 0:
            break
        g_reg = await _create_reg(is_host=False, state="confirmed", seats=1, guest_names=[name], waitlist_pos=None)
        await ledger_repo.apply_ledger_entry(
            db,
            user_id=user_id,
            kind="fee_capture",
            amount_cents=-fee,
            session_id=session_id,
            registration_id=g_reg.id,
            idempotency_key=f"cap:{g_reg.id}",
        )
        try:
            await add_outbox_event(
                db,
                channel=f"session:{session_id}",
                payload={"type": "registration_confirmed", "session_id": str(session_id), "registration_id": str(g_reg.id), "seats": 1},
            )
        except Exception:
            pass
        left -= 1
        confirmed_count += 1

    # Any remaining guests go to the tail of the waitlist with holds
    remaining_guests = gnames[confirmed_count:]
    if remaining_guests:
        pos = await _next_waitlist_pos(db, session_id)
        for idx, name in enumerate(remaining_guests):
            g_reg = await _create_reg(is_host=False, state="waitlisted", seats=1, guest_names=[name], waitlist_pos=pos + idx)
            await ledger_repo.apply_ledger_entry(
                db,
                user_id=user_id,
                kind="hold",
                amount_cents=fee,
                session_id=session_id,
                registration_id=g_reg.id,
                idempotency_key=f"hold:{g_reg.id}",
            )
            try:
                await add_outbox_event(
                    db,
                    channel=f"session:{session_id}",
                    payload={
                        "type": "registration_waitlisted",
                        "session_id": str(session_id),
                        "registration_id": str(g_reg.id),
                        "seats": 1,
                        "waitlist_pos": pos + idx,
                    },
                )
            except Exception:
                pass

    await db.commit()
    return ("confirmed", host_reg.id, None, created_reg_ids)
