from __future__ import annotations
import uuid
from datetime import datetime, timezone
from ..redis_client import redis
from ..observability.metrics import PROMOTED
    
def _k_promote(session_id: uuid.UUID) -> str:
    return f"promote:{session_id}:stream"

async def enqueue_promotion_check(session_id: uuid.UUID) -> None:
    await redis.xadd(
        _k_promote(session_id),
        fields={
            "ts": datetime.now(timezone.utc).isoformat()
        },
    )
    
    PROMOTED.labels(session_id=str(session_id)).inc()

