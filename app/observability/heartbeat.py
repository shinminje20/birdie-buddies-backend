from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from ..redis_client import redis

async def beat(key: str, interval_sec: int = 5, ttl_sec: int = 20):
    while True:
        try:
            await redis.set(key, datetime.now(timezone.utc).isoformat(), ex=ttl_sec)
        except Exception:
            pass
        await asyncio.sleep(interval_sec)
