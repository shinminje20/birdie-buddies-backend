from fastapi import APIRouter
from ...db import db_health
from ...redis_client import redis_health
from ...config import get_settings

router = APIRouter(prefix="/health", tags=["health"])
S = get_settings()

@router.get("")
async def health():
    db_ok, redis_ok = await db_health(), await redis_health()
    status = "ok" if (db_ok and redis_ok) else "degraded"
    return {
        "status": status,
        "dependencies": {
            "postgres": db_ok,
            "redis": redis_ok,
        },
    }

@router.get("/readiness")
async def readiness():
    # optional extra checks: redis ping and quick outbox backlog
    db_ok, redis_ok = await db_health(), await redis_health()
    # count pending outbox quickly (cheap heuristic)
    outbox_pending = "unknown"
    try:
        # Keep it extremely cheap; you can add a repo if you want exact counts.
        outbox_pending = "n/a"
    except Exception:
        pass
    return {"ready": bool(db_ok and redis_ok), "postgres": db_ok, "redis": redis_ok, "outbox": outbox_pending}

@router.get("/liveness")
async def liveness():
    return {"alive": True}
