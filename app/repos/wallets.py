from __future__ import annotations
import uuid
from dataclasses import dataclass
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from ..models import Wallet
import logging
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

log = logging.getLogger(__name__)

@dataclass
class WalletSummary:
    posted_cents: int
    holds_cents: int

    @property
    def available_cents(self) -> int:
        return self.posted_cents - self.holds_cents


# async def ensure_wallet_row(db: AsyncSession, user_id: uuid.UUID) -> None:
#     # Try insert default wallet; ignore if exists
#     print("0")
#     w = Wallet(user_id=user_id)  # defaults to 0s
#     print("1")
#     db.add(w)
#     print("2")
#     try:
#         await db.flush()
#     except Exception as e:
#         log.error(f" Error while ensure_wallet_row : {e}")
#         await db.rollback()
#         # In case of race, ensure we didn't break the outer txn; reattach
#         await db.begin()

from sqlalchemy.dialects.postgresql import insert

async def ensure_wallet_row(db: AsyncSession, user_id: uuid.UUID) -> None:
    stmt = (
        insert(Wallet)
        .values(user_id=user_id)
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    await db.execute(stmt)
    
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from ..models import Wallet
import uuid
async def ensure_and_lock_wallet(db: AsyncSession, user_id: uuid.UUID) -> Wallet:
    # Atomic “insert if missing”
    stmt = (
        pg_insert(Wallet)
        .values(user_id=user_id)  # server defaults fill posted/holds=0
        .on_conflict_do_nothing(index_elements=[Wallet.__table__.c.user_id])
    )
    await db.execute(stmt)

    # Lock the row for the rest of the txn
    wrow = await db.execute(
        select(Wallet).where(Wallet.user_id == user_id).with_for_update()
    )
    return wrow.scalar_one()  # now locked

async def get_wallet_summary(db: AsyncSession, user_id: uuid.UUID) -> WalletSummary:
    res = await db.execute(select(Wallet).where(Wallet.user_id == user_id))
    
    w = res.scalar_one_or_none()
    if not w:
        return WalletSummary(0, 0)
    return WalletSummary(w.posted_cents, w.holds_cents)
