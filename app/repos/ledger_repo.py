from __future__ import annotations
from typing import Optional, Sequence, Literal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, update, insert, func
from sqlalchemy.exc import IntegrityError
from ..models import LedgerEntry, Wallet
import uuid

# EXPECTED status per kind
_KIND_STATUS = {
    "hold": "held",
    "hold_release": "posted",
    "deposit_in": "posted",
    "refund": "posted",
    "fee_capture": "posted",
    "penalty": "posted",
}

# EXPECTED sign per kind: +1 means amount must be > 0, -1 means amount must be < 0
_KIND_SIGN = {
    "hold": +1,
    "hold_release": -1,
    "deposit_in": 0,
    "refund": +1,
    "fee_capture": -1,
    "penalty": -1,
}

async def apply_ledger_entry(
    db: AsyncSession,
    *,
    user_id,
    kind: str,
    amount_cents: int,
    session_id = None,
    registration_id = None,
    idempotency_key: str,
):
    """Insert a ledger row and mutate wallet totals idempotently.

    Rules:
      - kind determines expected status and amount sign.
      - 'hold' affects holds_cents (increase); 'hold_release' decreases holds_cents.
      - 'deposit_in' / 'refund' increase posted_cents.
      - 'fee_capture' / 'penalty' decrease posted_cents.
    """
    if kind not in _KIND_STATUS:
        raise ValueError(f"unknown ledger kind: {kind}")

    # normalize and validate sign
    if not isinstance(amount_cents, int):
        try:
            amount_cents = int(amount_cents)
        except Exception:
            raise ValueError("amount_cents must be int cents")

    expected = _KIND_SIGN[kind]
    if expected == +1 and amount_cents <= 0:
        raise ValueError(f"{kind} must use positive amount_cents")
    if expected == -1 and amount_cents >= 0:
        raise ValueError(f"{kind} must use negative amount_cents")

    status = _KIND_STATUS[kind]

    # 1) Idempotency: if the key already exists, return that row (UNCHANGED)
    if idempotency_key:
        res = await db.execute(
            select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key)
        )
        existing = res.scalar_one_or_none()
        if existing:
            return existing

    # Upsert wallet (UNCHANGED)
    wrow = await db.execute(
        select(Wallet).where(Wallet.user_id == user_id).with_for_update()
    )
    wallet = wrow.scalar_one_or_none()
    if wallet is None:
        await db.execute(
            insert(Wallet).values(user_id=user_id, posted_cents=0, holds_cents=0)
        )
        wrow = await db.execute(
            select(Wallet).where(Wallet.user_id == user_id).with_for_update()
        )
        wallet = wrow.scalar_one()

    # Mutations to wallet totals (UNCHANGED)
    delta_posted = 0
    delta_holds = 0
    if kind == "hold":
        delta_holds = amount_cents
    elif kind == "hold_release":
        delta_holds = amount_cents
    elif kind in ("deposit_in", "refund"):
        delta_posted = amount_cents
    elif kind in ("fee_capture", "penalty"):
        delta_posted = amount_cents

    # Write ledger row (MINIMAL CHANGE: try/except + final select+return)
    try:
        await db.execute(
            insert(LedgerEntry).values(
                user_id=user_id,
                session_id=session_id,
                registration_id=registration_id,
                idempotency_key=idempotency_key,
                kind=kind,
                amount_cents=amount_cents,
                status=status,
            )
        )
    except IntegrityError:
        # Another concurrent insert with the same idempotency_key won the race.
        # Roll back the failed statement and fall through to select the existing row.
        await db.rollback()

    # Apply wallet deltas (UNCHANGED)
    await db.execute(
        update(Wallet)
        .where(Wallet.user_id == user_id)
        .values(
            posted_cents = Wallet.posted_cents + delta_posted,
            holds_cents  = Wallet.holds_cents  + delta_holds,
            updated_at   = func.now(),
        )
    )

    # NEW: fetch and return the ledger row we just wrote (or the one that already existed)
    res = await db.execute(
        select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key)
    )
    
    entry = res.scalars().first()
    if entry is None:
        # Extremely unlikely unless the insert failed for a non-idempotency reason.
        # Let the caller decide how to handle—raising keeps behavior explicit.
        raise RuntimeError("Ledger entry was not created")
    return entry

LedgerKind = Literal["deposit_in","fee_hold","fee_capture","hold_release","refund","penalty"]
async def list_ledger_for_user(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    limit: int = 50,
    before_id: Optional[int] = None,
) -> Sequence[LedgerEntry]:
    q = select(LedgerEntry).where(LedgerEntry.user_id == user_id).order_by(desc(LedgerEntry.id)).limit(limit)
    if before_id:
        q = q.where(LedgerEntry.id < before_id)
    res = await db.execute(q)
    return list(res.scalars().all())


async def list_ledger_admin(
    db: AsyncSession,
    *,
    user_id: Optional[uuid.UUID] = None,
    session_id: Optional[uuid.UUID] = None,
    limit: int = 100,
    before_id: Optional[int] = None,
) -> Sequence[LedgerEntry]:
    q = select(LedgerEntry).order_by(desc(LedgerEntry.id)).limit(limit)
    if before_id:
        q = q.where(LedgerEntry.id < before_id)
    if user_id:
        q = q.where(LedgerEntry.user_id == user_id)
    if session_id:
        q = q.where(LedgerEntry.session_id == session_id)
    res = await db.execute(q)
    return list(res.scalars().all())