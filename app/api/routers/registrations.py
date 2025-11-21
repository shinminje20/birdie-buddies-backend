from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone
from typing import Optional, List

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Header, status, Query
from pydantic import BaseModel, Field, conint, validator

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, asc

from ...auth.deps import get_current_user
from ...db import get_db
from ...models import User, Session as SessionModel, Registration, User
from ...redis_client import redis
from ...services.cancellation import cancel_registration
from fastapi import Request
from ...services.rate_limit import check_backlog_or_429, inc_backlog, limit_registration
from ...services.guest_update import update_guest_list, Forbidden as GUForbidden, NotFound as GUNotFound, InvalidChange as GUInvalidChange, TooLate as GUTooLate
from ...observability.metrics import REG_ENQUEUED

from ...auth.deps import get_current_user
from ...models import Registration, User, Session as SessionModel

from ...services.guest_add import add_guest_registration, Forbidden as GAForbidden, NotFound as GANotFound, InvalidState as GAInvalid, LimitExceeded as GALimit, InsufficientFunds as GAFunds, TooLate as GATooLate

router = APIRouter(tags=["registrations"])

# Redis keys
def _k_stream(session_id: uuid.UUID) -> str:        return f"sess:{session_id}:stream"
def _k_idemp(session_id: uuid.UUID, user_id: uuid.UUID, key: str) -> str:
    return f"idemp:{session_id}:{user_id}:{key}"
def _k_req(req_id: str) -> str:                     return f"req:{req_id}:status"

IDEMP_TTL_SEC = 15 * 60
REQ_TTL_SEC   = 24 * 60 * 60


from pydantic import BaseModel, Field
from ...domain.schemas.registration import RegisterIn, RegisterEnqueuedOut, RegRowOut, RequestStatusOut, GuestsUpdateIn, GuestsUpdateOut, CancelOut, MyRegistrationOut

@router.get("/me/registrations", response_model=list[MyRegistrationOut])
async def my_registrations(
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    show_past: bool = Query(default=False, description="Show past/closed sessions instead of upcoming"),
):
    # Build query based on whether we want past or upcoming sessions
    if show_past:
        # Show closed sessions (past registrations)
        rows = await db.execute(
            select(Registration, SessionModel)
            .join(SessionModel, SessionModel.id == Registration.session_id)
            .where(
                Registration.host_user_id == current.id,
                Registration.state != "canceled",
                SessionModel.status == "closed",  # Only closed sessions
            )
            .order_by(SessionModel.starts_at.desc())  # Most recent first
        )
    else:
        # Show upcoming sessions (not closed)
        rows = await db.execute(
            select(Registration, SessionModel)
            .join(SessionModel, SessionModel.id == Registration.session_id)
            .where(
                Registration.host_user_id == current.id,
                Registration.state != "canceled",
                SessionModel.status != "closed",  # Exclude closed sessions
            )
            .order_by(SessionModel.starts_at.asc())  # Upcoming sessions first
        )
    out: list[MyRegistrationOut] = []
    for reg, sess in rows.all():
        out.append(
            MyRegistrationOut(
                registration_id=reg.id,
                session_id=sess.id,
                session_title=sess.title,
                starts_at_utc=sess.starts_at,
                timezone=sess.timezone,
                session_status=sess.status,
                seats=reg.seats,
                guest_names=reg.guest_names or [],
                waitlist_pos=reg.waitlist_pos,
                state=reg.state,
            )
        )
    return out


@router.post("/sessions/{session_id}/register", response_model=RegisterEnqueuedOut, status_code=status.HTTP_202_ACCEPTED)
async def enqueue_registration(
    session_id: uuid.UUID,
    payload: RegisterIn,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    request: Request = None,
):

    # 0) Backlog cap (fast reject if queue is saturated)
    await check_backlog_or_429(session_id)

    # 0.1) Rate limit (per-IP and per-user)
    await limit_registration(request, current.id)
    
    # 1) Validate session exists & is schedulable
    res = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
    sess = res.scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    if sess.status != "scheduled":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"session not open for registration: {sess.status}")

    # 2) Prevent duplicate active registrations by same host (confirmed or waitlisted)
    
    dup = await db.execute(
        select(Registration.id).where(
            Registration.session_id == session_id,
            Registration.host_user_id == current.id,
            Registration.state != "canceled",
        )
    )
    val = dup.scalar_one_or_none()
    if val:
        print("val.id: ", val)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already registered or waitlisted")
    
    # 3) Idempotency: map key -> existing request_id if present
    idemp_key = _k_idemp(session_id, current.id, idempotency_key.strip())
    req_id = await redis.get(idemp_key)
    if not req_id:
        req_id = str(uuid.uuid4())
        # store idempotency with TTL
        await redis.set(idemp_key, req_id, ex=IDEMP_TTL_SEC)

        # 4) Persist a minimal request status (so GET /requests/{id} works immediately)
        req_key = _k_req(req_id)
        created_at = datetime.now(timezone.utc).isoformat()
        
        await redis.hset(
            req_key,
            mapping={
                "state": "queued",
                "session_id": str(session_id),
                "user_id": str(current.id),
                "seats": str(payload.seats),
                "guest_names": json.dumps(payload.guest_names),
                "created_at": created_at,
            },
        )
        
        await redis.expire(req_key, REQ_TTL_SEC)

        # 5) Enqueue to per-session Redis Stream (server-ordered)
        await redis.xadd(
            _k_stream(session_id),
            fields={
                "request_id": req_id,
                "user_id": str(current.id),
                "seats": str(payload.seats),
                "guest_names": json.dumps(payload.guest_names),
                "idempotency_key": idempotency_key.strip(),
                "ts": created_at,
            },
        )
        
        REG_ENQUEUED.labels(session_id=str(session_id)).inc()
        
        # 5.1) Track backlog size for this session
        await inc_backlog(session_id)

    return RegisterEnqueuedOut(request_id=req_id)

@router.get("/requests/{request_id}/status", response_model=RequestStatusOut)
async def get_request_status(request_id: str):
    req_key = _k_req(request_id)
    data = await redis.hgetall(req_key)
    if not data:
        # Could be already processed and GC'd; for now, treat as not found
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="request not found")

    try:
        return RequestStatusOut(
            state=data["state"],
            session_id=uuid.UUID(data["session_id"]),
            user_id=uuid.UUID(data["user_id"]),
            seats=int(data["seats"]),
            guest_names=json.loads(data["guest_names"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            registration_id=uuid.UUID(data["registration_id"]) if data.get("registration_id") else None,
            waitlist_pos=int(data["waitlist_pos"]) if data.get("waitlist_pos") else None,
        )
    except Exception:
        # If bad shape, surface a consistent error
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="corrupt request status")

@router.post("/registrations/{registration_id}/cancel", response_model=CancelOut)
async def cancel(
    registration_id: uuid.UUID,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        refund_cents, penalty_cents, state = await cancel_registration(
            db,
            registration_id=registration_id,
            caller_user_id=current.id,
            caller_is_admin=current.is_admin,
        )
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    if state == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="registration not found")
    if state == "too_late":
        # You can switch to 409 if you prefer
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="cannot cancel after session start")
    if state == "session_closed":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="cannot cancel when session closed")

    return CancelOut(refund_cents=refund_cents, penalty_cents=penalty_cents, state=state)

# List registrations for a session (participants + waitlist)
# Add a read endpoint so the UI can display the current participants & waitlist.
# This endpoint is public; add Depends(get_current_user) if you want to require auth to view participants.
# Restart the API after these changes.
@router.get("/sessions/{session_id}/registrations", response_model=list[RegRowOut])
async def list_regs_for_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    rows = await db.execute(
        select(Registration, User.name)
        .join(User, User.id == Registration.host_user_id)
        .where(Registration.session_id == session_id)
        .order_by(
            # confirmed first by time, then waitlist order
            asc(Registration.created_at)
        )
    )
    result: list[RegRowOut] = []
    for reg, host_name in rows.all():
        result.append(
            RegRowOut(
                registration_id=reg.id,
                host_user_id=reg.host_user_id,
                host_name=host_name,
                seats=reg.seats,
                guest_names=reg.guest_names or [],
                waitlist_pos=reg.waitlist_pos,
                state=reg.state,
            )
        )
    # stable ordering for UI: confirmed first, then waitlist by pos
    result.sort(key=lambda r: (0 if r.state=="confirmed" else 1, r.waitlist_pos or 0))
    return result

@router.patch("/registrations/{registration_id}/guests", response_model=GuestsUpdateOut)
async def patch_guests(
    registration_id: uuid.UUID,
    payload: GuestsUpdateIn,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        old_seats, new_seats, refund_cents, penalty_cents, state = await update_guest_list(
            db,
            registration_id=registration_id,
            caller_user_id=current.id,
            caller_is_admin=current.is_admin,
            new_guest_names=payload.guest_names,
        )
    except GUNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="registration not found")
    except GUForbidden:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    except GUTooLate:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="cannot modify after session start")
    except GUInvalidChange as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return GuestsUpdateOut(
        registration_id=registration_id,
        old_seats=old_seats,
        new_seats=new_seats,
        refund_cents=refund_cents,
        penalty_cents=penalty_cents,
        state=state,
    )
    
    

class GuestAddIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)

class GuestAddOut(BaseModel):
    registration_id: uuid.UUID
    state: str
    waitlist_pos: int | None = None

@router.post("/registrations/{host_registration_id}/guests", response_model=GuestAddOut, status_code=status.HTTP_201_CREATED)
async def add_guest(
    host_registration_id: uuid.UUID,
    payload: GuestAddIn,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        reg_id, state, pos = await add_guest_registration(
            db,
            host_registration_id=host_registration_id,
            guest_name=payload.name,
            caller_user_id=current.id,
            caller_is_admin=current.is_admin,
        )
    except GANotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="registration not found")
    except GAForbidden:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    except GALimit:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="maximum 2 guests per host")
    except GAFunds:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="insufficient funds")
    except GAInvalid as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except GATooLate:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="cannot add after session start")

    return GuestAddOut(registration_id=reg_id, state=state, waitlist_pos=pos)
