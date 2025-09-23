from __future__ import annotations
import uuid
from typing import Optional, List, Literal
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, constr
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from ...auth.deps import get_current_user
from ...db import get_db
from ...models import User, Wallet, LedgerEntry, Registration, Session as SessionModel

router = APIRouter(prefix="/admin/users", tags=["admin:users"])

def _require_admin(u: User) -> None:
    if not u.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")


# ---------- Schemas ----------
class AdminUserRow(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    phone: Optional[str] = None
    is_admin: bool
    status: str
    created_at: str
    posted_cents: int
    holds_cents: int
    available_cents: int

class AdminUserListOut(BaseModel):
    items: List[AdminUserRow]
    total: int

class AdminUserWallet(BaseModel):
    posted_cents: int
    holds_cents: int
    available_cents: int

class AdminLedgerRow(BaseModel):
    id: int
    kind: str
    amount_cents: int
    session_id: Optional[uuid.UUID] = None
    registration_id: Optional[uuid.UUID] = None
    created_at: str

class AdminRegistrationRow(BaseModel):
    registration_id: uuid.UUID
    session_id: uuid.UUID
    session_title: Optional[str] = None
    starts_at_utc: str
    timezone: str
    seats: int
    guest_names: List[str] = []
    state: str
    waitlist_pos: Optional[int] = None
    created_at: str
    canceled_at: Optional[str] = None

class AdminUserDetailOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    phone: Optional[str] = None
    is_admin: bool
    status: str
    created_at: str
    wallet: AdminUserWallet
    ledger: List[AdminLedgerRow]
    registrations: List[AdminRegistrationRow]

class AdminUserUpdateIn(BaseModel):
    name: Optional[constr(strip_whitespace=True, min_length=2)] = None
    email: Optional[EmailStr] = None
    phone: Optional[constr(strip_whitespace=True, min_length=5, max_length=40)] = None
    status: Optional[Literal["active", "disabled"]] = None
    is_admin: Optional[bool] = None



# ---------- Endpoints ----------

@router.get("", response_model=AdminUserListOut)
async def admin_list_users(
    q: Optional[str] = Query(None, description="search name or email"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    _require_admin(current)

    # base query
    # cond = []
    # if q:
    #     qs = f"%{q.lower()}%"
    #     cond.append(sa.or_(func.lower(User.name).like(qs), func.lower(User.email).like(qs)))
    
    # List (add User.deleted_at.is_(None) to the WHERE)
    cond = [User.deleted_at.is_(None)]
    if q:
        qs = f"%{q.lower()}%"
        cond.append(sa.or_(func.lower(User.name).like(qs), func.lower(User.email).like(qs)))

    # total
    total = (await db.execute(select(func.count()).select_from(select(User.id).where(*cond).subquery()))).scalar_one()

    # rows with wallet join
    rows = await db.execute(
        select(
            User.id, User.name, User.email, User.phone, User.is_admin, User.status, User.created_at,
            func.coalesce(Wallet.posted_cents, 0),
            func.coalesce(Wallet.holds_cents, 0),
            (func.coalesce(Wallet.posted_cents, 0) - func.coalesce(Wallet.holds_cents, 0)).label("available_cents"),
        )
        .select_from(User)
        .join(Wallet, Wallet.user_id == User.id, isouter=True)
        .where(*cond)
        .order_by(User.created_at.desc())
        .limit(limit).offset(offset)
    )
    items = [
        AdminUserRow(
            id=r[0], name=r[1], email=r[2], phone=r[3], is_admin=r[4], status=r[5], created_at=r[6].isoformat(),
            posted_cents=r[7] or 0, holds_cents=r[8] or 0, available_cents=r[9] or 0
        )
        for r in rows.all()
    ]

    return AdminUserListOut(items=items, total=total)


@router.get("/{user_id}", response_model=AdminUserDetailOut)
async def admin_get_user(
    user_id: uuid.UUID,
    ledger_limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    _require_admin(current)

    # user + wallet
    urow = await db.execute(
        select(
            User.id, User.name, User.email, User.phone, User.is_admin, User.status, User.created_at,
            func.coalesce(Wallet.posted_cents, 0), func.coalesce(Wallet.holds_cents, 0)
        )
        .join(Wallet, Wallet.user_id == User.id, isouter=True)
        .where(User.id == user_id)
        
        # If don't want to let the Admin look up deleted account, Uncomment this, and replace with above.
        # .where(User.id == user_id, User.deleted_at.is_(None))
    )
    u = urow.first()
    if not u:
        raise HTTPException(status_code=404, detail="user not found")

    wallet = AdminUserWallet(
        posted_cents=u[7] or 0,
        holds_cents=u[8] or 0,
        available_cents=(u[7] or 0) - (u[8] or 0),
    )

    # ledger (most recent first)
    lrows = await db.execute(
        select(LedgerEntry)
        .where(LedgerEntry.user_id == user_id)
        .order_by(LedgerEntry.id.desc())
        .limit(ledger_limit)
    )
    ledger = [
        AdminLedgerRow(
            id=le.id,
            kind=le.kind,
            amount_cents=le.amount_cents,
            session_id=le.session_id,
            registration_id=le.registration_id,
            created_at=le.created_at.isoformat(),
        )
        for le in lrows.scalars().all()
    ]

    # registrations with session info
    rrows = await db.execute(
        select(Registration, SessionModel)
        .join(SessionModel, SessionModel.id == Registration.session_id)
        .where(Registration.host_user_id == user_id)
        .order_by(Registration.created_at.desc())
    )
    regs: list[AdminRegistrationRow] = []
    for reg, sess in rrows.all():
        regs.append(
            AdminRegistrationRow(
                registration_id=reg.id,
                session_id=sess.id,
                session_title=getattr(sess, "title", None),
                starts_at_utc=sess.starts_at.isoformat(),
                timezone=sess.timezone,
                seats=reg.seats,
                guest_names=reg.guest_names or [],
                state=reg.state,
                waitlist_pos=reg.waitlist_pos,
                created_at=reg.created_at.isoformat(),
                canceled_at=reg.canceled_at.isoformat() if reg.canceled_at else None,
            )
        )

    return AdminUserDetailOut(
        id=u[0], name=u[1], email=u[2], phone=u[3], is_admin=u[4], status=u[5], created_at=u[6].isoformat(),
        wallet=wallet,
        ledger=ledger,
        registrations=regs,
    )

@router.patch("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_update_user(
    user_id: uuid.UUID,
    payload: AdminUserUpdateIn,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    _require_admin(current)

    # Fetch target user
    row = await db.execute(select(User).where(User.id == user_id))
    target: User | None = row.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Self-demotion guard
    if payload.is_admin is not None and current.id == target.id and payload.is_admin is False:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot demote yourself")

    # Apply allowed fields
    if payload.name is not None:
        target.name = payload.name.strip()
    if payload.email is not None:
        target.email = payload.email.lower()
    if payload.phone is not None:
        target.phone = payload.phone.strip()
    if payload.status is not None:
        target.status = payload.status
    if payload.is_admin is not None:
        # Last-admin protection if turning off admin
        if target.is_admin and payload.is_admin is False:
            # Count other admins (active & not deleted if you track deleted_at)
            q = select(func.count()).select_from(User).where(
                User.is_admin.is_(True),
                User.id != target.id,
                # If you have these columns:
                User.status == "active",
                # User.deleted_at.is_(None),
            )
            count_other_admins = (await db.execute(q)).scalar_one()
            if count_other_admins == 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cannot demote the last remaining admin",
                )
        target.is_admin = bool(payload.is_admin)

    # Commit with conflict handling (unique email/phone)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Surface as 409 Conflict without leaking DB internals
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email or phone already in use",
        )

    # 204 No Content; admin UI should refetch the detail view
    return

@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_soft_delete_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    _require_admin(current)

    # Fetch target
    row = await db.execute(select(User).where(User.id == user_id))
    target: User | None = row.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Last-admin guard (cannot delete final admin)
    if target.is_admin:
        other_admins = (await db.execute(
            select(func.count()).select_from(User).where(
                User.is_admin.is_(True),
                User.id != target.id,
                # If you track active-only admins, uncomment:
                User.status == "active",
                # User.deleted_at.is_(None),
            )
        )).scalar_one()
        if other_admins == 0:
            raise HTTPException(status_code=409, detail="Cannot delete the last remaining admin")

    # Soft delete: mark disabled + deleted_at
    target.status = "disabled"
    target.deleted_at = datetime.now(timezone.utc)

    await db.commit()
    return