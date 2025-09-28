from __future__ import annotations
import uuid
from typing import Optional, List, Annotated
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_db
from ...models import User, LedgerEntry
from ...auth.deps import get_current_user
from ...repos import ledger_repo as ledger_repo

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(u: User) -> None:
    if not u.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")


class DepositIn(BaseModel):
    user_id: uuid.UUID
    amount_cents: int
    idempotency_key: Optional[Annotated[str, StringConstraints(min_length=6, max_length=120, strip_whitespace=True)]] = None
    note: Optional[str] = None  # reserved for future (a separate notes table if needed)


class LedgerOut(BaseModel):
    id: int
    kind: str
    amount_cents: int
    user_id: uuid.UUID
    session_id: Optional[uuid.UUID] = None
    registration_id: Optional[uuid.UUID] = None
    created_at: str

    @classmethod
    def from_model(cls, e: LedgerEntry) -> LedgerOut:
        return cls(
            id=e.id,
            kind=e.kind,
            amount_cents=e.amount_cents,
            user_id=e.user_id,
            session_id=e.session_id,
            registration_id=e.registration_id,
            created_at=e.created_at.isoformat(),
        )


@router.post("/deposits", response_model=LedgerOut)
async def deposit(
    payload: DepositIn,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    _require_admin(current)

    # Add deposit (positive to user)
    entry = await ledger_repo.apply_ledger_entry(
        db,
        user_id=payload.user_id,
        kind="deposit_in",
        amount_cents=payload.amount_cents,
        idempotency_key=payload.idempotency_key,
    )
    await db.commit()
    return LedgerOut.from_model(entry)


@router.get("/ledger", response_model=List[LedgerOut])
async def ledger_admin(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
    user_id: Optional[uuid.UUID] = Query(default=None),
    session_id: Optional[uuid.UUID] = Query(default=None),
    limit: Annotated[int, Field(gt=0, le=500)] = 100,
    before_id: Optional[int] = Query(default=None),
):
    _require_admin(current)
    rows = await ledger_repo.list_ledger_admin(
        db, user_id=user_id, session_id=session_id, limit=limit, before_id=before_id
    )
    return [
        LedgerOut.from_model(e) for e in rows
    ]
