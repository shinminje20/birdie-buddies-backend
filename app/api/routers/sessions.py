from __future__ import annotations
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, conint, validator, confloat
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_db
from ...models import User, Session as SessionModel
from ...auth.deps import get_current_user
from ...repos import sessions as sess_repo
from ...services.promotion import enqueue_promotion_check
from ...services.session_lifecycle import admin_update_session, InvalidTransition, CapacityBelowConfirmed, NotFound

router = APIRouter(tags=["sessions"])


# ---------- Pydantic models ----------
class SessionCreateIn(BaseModel):
    title: str | None = None
    starts_at_utc: datetime
    timezone: str
    capacity: conint(gt=0)
    fee_cents: confloat(ge=0)

    @validator("starts_at_utc")
    def tzaware_and_utc(cls, v: datetime):
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("starts_at_utc must be timezone-aware (UTC)")
        if v.utcoffset() != timezone.utc.utcoffset(v):
            raise ValueError("starts_at_utc must be in UTC (e.g., '2025-08-15T02:00:00Z')")
        return v

    @validator("timezone")
    def valid_tz(cls, v: str):
        # Ensure IANA tz is valid
        try:
            ZoneInfo(v)
        except Exception:
            raise ValueError("invalid IANA timezone")
        return v


class SessionOut(BaseModel):
    id: uuid.UUID
    title: str | None
    starts_at_utc: datetime
    timezone: str
    capacity: int
    fee_cents: float
    status: str
    created_at: datetime

    @classmethod
    def from_model(cls, s: SessionModel) -> "SessionOut":
        return cls(
            id=s.id,
            title=s.title,
            starts_at_utc=s.starts_at,
            timezone=s.timezone,
            capacity=s.capacity,
            fee_cents=s.fee_cents,
            status=s.status,
            created_at=s.created_at,
        )


class SessionWithStatsOut(SessionOut):
    confirmed_seats: int
    remaining_seats: int


class SessionPatchIn(BaseModel):
    capacity: conint(gt=0) | None = None
    status: str | None = Field(default=None, description="'scheduled' | 'closed' | 'canceled'")

    @validator("status")
    def valid_status(cls, v):
        if v is None:
            return v
        if v not in ("scheduled", "closed", "canceled"):
            raise ValueError("status must be one of 'scheduled','closed','canceled'")
        return v


# ---------- Helpers ----------
def _require_admin(u: User) -> None:
    if not u.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")


def _to_stats(s: SessionModel, confirmed: int) -> SessionWithStatsOut:
    remaining = max(0, s.capacity - confirmed)
    base = SessionOut.from_model(s).dict()
    return SessionWithStatsOut(**base, confirmed_seats=confirmed, remaining_seats=remaining)


# ---------- Public ----------
@router.get("/sessions", response_model=list[SessionWithStatsOut])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    limit: conint(gt=0, le=200) = Query(default=50),
):
    now_utc = datetime.now(timezone.utc)
    rows = await sess_repo.list_upcoming(db, now_utc=now_utc, limit=limit)
    return [_to_stats(s, confirmed) for (s, confirmed) in rows]


@router.get("/sessions/{session_id}", response_model=SessionWithStatsOut)
async def get_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    row = await sess_repo.get_with_counts(db, session_id=session_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    s, confirmed = row
    return _to_stats(s, confirmed)


# ---------- Admin ----------
@router.post("/admin/sessions", response_model=SessionOut)
async def create_session(
    payload: SessionCreateIn,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    _require_admin(current)
    s = await sess_repo.create_session(
        db,
        title=payload.title,
        starts_at_utc=payload.starts_at_utc.astimezone(timezone.utc),
        timezone_name=payload.timezone,
        capacity=payload.capacity,
        fee_cents=payload.fee_cents,
    )
    await db.commit()
    return SessionOut.from_model(s)

@router.patch("/admin/sessions/{session_id}", response_model=SessionOut)
async def patch_session(
    session_id: uuid.UUID,
    payload: SessionPatchIn,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    _require_admin(current)
    try:
        s = await admin_update_session(
            db,
            session_id=session_id,
            new_capacity=payload.capacity,
            new_status=payload.status,
        )
    except NotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    except CapacityBelowConfirmed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="capacity cannot be set below currently confirmed seats",
        )
    except InvalidTransition as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return SessionOut.from_model(s)


# @router.patch("/admin/sessions/{session_id}", response_model=SessionOut)
# async def patch_session(
#     session_id: uuid.UUID,
#     payload: SessionPatchIn,
#     db: AsyncSession = Depends(get_db),
#     current: User = Depends(get_current_user),
# ):
#     _require_admin(current)
#     try:
#         s = await sess_repo.update_session(
#             db,
#             session_id=session_id,
#             capacity=payload.capacity,
#             status=payload.status,
#         )
#     except ValueError as e:
#         if str(e) == "capacity_below_confirmed":
#             raise HTTPException(
#                 status_code=status.HTTP_409_CONFLICT,
#                 detail="capacity cannot be set below currently confirmed seats",
#             )
#         raise
#     if not s:
#         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
#     await db.commit()
#     await enqueue_promotion_check(s.id)
#     return SessionOut.from_model(s)
