from __future__ import annotations
import uuid
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field, conint
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_db
from ...models import User, LedgerEntry
from ...auth.deps import get_current_user
from ...repos.wallets import get_wallet_summary
from ...repos import ledger as ledger_repo

router = APIRouter(prefix="/wallet", tags=["wallet"])


class WalletOut(BaseModel):
    posted_cents: int
    holds_cents: int
    available_cents: int


class LedgerOut(BaseModel):
    id: int
    kind: str
    amount_cents: int
    session_id: Optional[uuid.UUID] = None
    registration_id: Optional[uuid.UUID] = None
    created_at: str

    @classmethod
    def from_model(cls, e: LedgerEntry) -> "LedgerOut":
        return cls(
            id=e.id,
            kind=e.kind,
            amount_cents=e.amount_cents,
            session_id=e.session_id,
            registration_id=e.registration_id,
            created_at=e.created_at.isoformat(),
        )


@router.get("/me", response_model=WalletOut)
async def my_wallet(current: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    s = await get_wallet_summary(db, current.id)
    return WalletOut(posted_cents=s.posted_cents, holds_cents=s.holds_cents, available_cents=s.available_cents)


@router.get("/me/ledger", response_model=List[LedgerOut])
async def my_ledger(
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: conint(gt=0, le=200) = 50,
    before_id: Optional[int] = Query(default=None),
):
    rows = await ledger_repo.list_ledger_for_user(db, user_id=current.id, limit=limit, before_id=before_id)
    return [LedgerOut.from_model(e) for e in rows]
