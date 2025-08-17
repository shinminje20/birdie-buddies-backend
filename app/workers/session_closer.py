from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone

from ..config import get_settings
from ..db import SessionLocal
from ..redis_client import redis
from ..services.session_auto_close import close_due_sessions
from ..observability.heartbeat import beat  # from Step 12

S = get_settings()
log = logging.getLogger("worker.session_closer")

def _lock_key() -> str: return "lock:session_closer"

async def _acquire_lock() -> bool:
    # Only one instance performs the scan; others idle
    return await redis.set(_lock_key(), "1", ex=S.AUTO_CLOSE_LOCK_TTL_SEC, nx=True) is True

async def run_once():
    # Acquire short lock; if taken, just skip this tick
    if not await _acquire_lock():
        return 0
    async with SessionLocal() as db:
        closed = await close_due_sessions(db, batch=S.AUTO_CLOSE_BATCH)
    if closed:
        log.info(f"auto-closed {len(closed)} sessions")
    return len(closed)

async def run_forever():
    # heartbeat for ops
    asyncio.create_task(beat("hb:session_closer"))
    while True:
        try:
            await run_once()
        except Exception as e:
            log.exception("session_closer error: %s", e)
        await asyncio.sleep(S.AUTO_CLOSE_INTERVAL_SEC)

def main():
    asyncio.run(run_forever())

if __name__ == "__main__":
    main()
