from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.domain.errors import *
from app.domain.schemas.registration import AdminPreregItemIn, AdminPreregResultOut
from repos import ledger_repo
from repos.wallets import ensure_and_lock_wallet, get_wallet_summary
from models import User, Session as SessionModel, Registration

async def prereg_batch_on_create(db: AsyncSession, *, session, items: list[AdminPreregItemIn]) -> list[AdminPreregResultOut]:
    results = []
    for item in items:
        results.append(await _preregister_one(db, session=session, item=item))
    return results

async def _preregister_one(
    db: AsyncSession,
    *,
    session: SessionModel,
    item: AdminPreregItemIn,
) -> AdminPreregResultOut:
    # A) user must exist and be active/not deleted
    urow = await db.execute(select(User).where(User.id == item.user_id))
    user = urow.scalar_one_or_none()
    if not user or getattr(user, "deleted_at", None) is not None or user.status != "active":
        return AdminPreregResultOut(user_id=item.user_id, state="rejected", error="user_disabled_or_missing")

    # B) session must be schedulable
    if session.status != "scheduled":
        return AdminPreregResultOut(user_id=item.user_id, state="rejected", error=f"session_{session.status}")

    # C) prevent duplicate active reg by same host
    dup = await db.execute(
        select(Registration.id).where(
            Registration.session_id == session.id,
            Registration.host_user_id == item.user_id,
            Registration.state != "canceled",
        )
    )
    if dup.scalar_one_or_none():
        return AdminPreregResultOut(user_id=item.user_id, state="rejected", error="already_registered_or_waitlisted")

    # D) lock session row → deterministic seat math
    srow = await db.execute(
        select(SessionModel).where(SessionModel.id == session.id).with_for_update()
    )
    s_locked = srow.scalar_one()

    # E) compute remaining confirmed seats
    used = await db.execute(
        select(func.coalesce(func.sum(Registration.seats), 0)).where(
            Registration.session_id == s_locked.id,
            Registration.state == "confirmed",
        )
    )
    confirmed_seats = int(used.scalar_one())
    remaining = max(0, s_locked.capacity - confirmed_seats)
    will_confirm = item.seats <= remaining

    # F) strict funds required
    total_fee = s_locked.fee_cents * item.seats
    await ensure_and_lock_wallet(db, item.user_id)  # creates & locks wallet row
    summary = await get_wallet_summary(db, item.user_id)
    if summary.available_cents < total_fee:
        return AdminPreregResultOut(user_id=item.user_id, state="rejected", error="insufficient_funds")

    # G) waitlist position if needed
    waitlist_pos = None
    if not will_confirm:
        wrow = await db.execute(
            select(func.coalesce(func.max(Registration.waitlist_pos), 0)).where(
                Registration.session_id == s_locked.id,
                Registration.state == "waitlisted",
            )
        )
        waitlist_pos = int(wrow.scalar_one()) + 1

    # H) create registration
    reg = Registration(
        session_id=s_locked.id,
        host_user_id=item.user_id,
        is_host=True,
        seats=item.seats,
        guest_names=item.guest_names,
        state="confirmed" if will_confirm else "waitlisted",
        waitlist_pos=None if will_confirm else waitlist_pos,
    )
    db.add(reg)
    await db.flush()  # need reg.id for ledger linkage

    # I) ledger / wallet movements (negative amounts = charge user)
    if will_confirm:
        await ledger_repo.apply_ledger_entry(
            db,
            user_id=item.user_id,
            session_id=s_locked.id,
            registration_id=reg.id,
            kind="fee_capture",
            amount_cents=-total_fee,
            idempotency_key=item.idempotency_key,
        )
    else:
        await ledger_repo.apply_ledger_entry(
            db,
            user_id=item.user_id,
            session_id=s_locked.id,
            registration_id=reg.id,
            kind="hold",
            amount_cents=-total_fee,
            idempotency_key=item.idempotency_key,
        )

    # (Optional) enqueue outbox event for realtime updates, e.g. session:{id}

    return AdminPreregResultOut(
        user_id=item.user_id,
        registration_id=reg.id,
        state="confirmed" if will_confirm else "waitlisted",
        waitlist_pos=None if will_confirm else waitlist_pos,
        error=None,
    )
