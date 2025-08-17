from __future__ import annotations
import uuid
from typing import Optional
from fastapi import HTTPException, Request, status
from ..config import get_settings
from ..redis_client import redis

S = get_settings()

# ---- generic token counter (fixed window) ----
async def _hit(key: str, window_sec: int, limit: int) -> None:
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window_sec)
    if count > limit:
        ttl = await redis.ttl(key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
            headers={"Retry-After": str(max(ttl, 1)) if ttl and ttl > 0 else "10"},
        )

def _client_ip(req: Request) -> str:
    # prefer X-Forwarded-For (first hop), fallback to uvicorn client
    h = req.headers.get("x-forwarded-for")
    if h:
        return h.split(",")[0].strip()
    return req.client.host if req.client else "unknown"

# ---- public helpers ----
async def limit_otp_request(req: Request) -> None:
    ip = _client_ip(req)
    await _hit(f"rl:otp:req:ip:{ip}", window_sec=10, limit=S.RL_OTP_REQ_PER_IP_10S)

async def limit_otp_verify(req: Request) -> None:
    ip = _client_ip(req)
    await _hit(f"rl:otp:verify:ip:{ip}", window_sec=10, limit=S.RL_OTP_VERIFY_PER_IP_10S)

async def limit_registration(req: Request, user_id: uuid.UUID) -> None:
    ip = _client_ip(req)
    # per-IP and per-user buckets
    await _hit(f"rl:reg:ip:{ip}", window_sec=10, limit=S.RL_REG_PER_IP_10S)
    await _hit(f"rl:reg:user:{user_id}", window_sec=10, limit=S.RL_REG_PER_USER_10S)

# ---- backlog cap for registration queue ----
def _k_backlog(session_id: uuid.UUID) -> str:
    return f"sess:{session_id}:backlog"

async def check_backlog_or_429(session_id: uuid.UUID) -> None:
    # number of unprocessed registration messages (maintained by enqueue+worker)
    v = await redis.get(_k_backlog(session_id))
    backlog = int(v) if v and v.isdigit() else 0
    if backlog >= S.REGISTRATION_QUEUE_MAX:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="registration queue is busy; try again shortly",
        )

async def inc_backlog(session_id: uuid.UUID) -> None:
    await redis.incr(_k_backlog(session_id))

async def dec_backlog(session_id: uuid.UUID) -> None:
    # never go below zero
    newv = await redis.decr(_k_backlog(session_id))
    if newv < 0:
        await redis.set(_k_backlog(session_id), 0)
