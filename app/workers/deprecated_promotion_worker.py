from __future__ import annotations
import argparse
import asyncio, socket
import os
import uuid
from typing import Any, Dict, Set

from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..redis_client import redis
from ..services.waitlist_promotion import promote_waitlist_fifo

from ..observability.heartbeat import beat


# Keys
def k_promote(session_id: uuid.UUID) -> str:   return f"promote:{session_id}:stream"
def k_req(req_id: str) -> str:                 return f"req:{req_id}:status"
def k_reg2req(reg_id: uuid.UUID) -> str:       return f"regreq:{reg_id}"

REQ_TTL_SEC = 24 * 60 * 60
GROUP = "g1"


async def _ensure_group(stream: str, group: str) -> None:
    try:
        await redis.xgroup_create(stream, group, id="$", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" in str(e):
            return
        raise


async def _set_status_confirmed(reg_id: uuid.UUID) -> None:
    # If we can find the original request id, mark it confirmed
    req_id = await redis.get(k_reg2req(reg_id))
    if not req_id:
        return
    await redis.hset(k_req(req_id), mapping={"state": "confirmed", "waitlist_pos": ""})
    await redis.expire(k_req(req_id), REQ_TTL_SEC)


async def handle_message(session_id: uuid.UUID, msg_id: str) -> None:
    # Run a promotion pass
    async with SessionLocal() as db:  # type: AsyncSession
        promoted = await promote_waitlist_fifo(db, session_id=session_id)

    # Update statuses
    for reg_id, _seats in promoted:
        await _set_status_confirmed(reg_id)
        
        # also push over Pub/Sub for SSE consumers
        req_id = await redis.get(k_reg2req(reg_id))
        if req_id:
            await redis.publish(k_req(req_id), json.dumps({"state": "confirmed", "registration_id": str(reg_id)}))

    # Ack message
    await redis.xack(k_promote(session_id), GROUP, msg_id)


async def worker_loop(session_id: uuid.UUID, consumer: str) -> None:
    stream = k_promote(session_id)
    await _ensure_group(stream, GROUP)

    while True:
        resp = await redis.xreadgroup(GROUP, consumer, streams={stream: ">"}, count=10, block=5000)
        if not resp:
            continue
        _, messages = resp[0]
        for msg_id, _fields in messages:
            try:
                await handle_message(session_id, msg_id)
            except Exception:
                # best effort; don't ack so it can be retried
                await asyncio.sleep(0.2)

def parse_args():
    p = argparse.ArgumentParser(description="Registration allocation worker")
    p.add_argument("--session-id", help="UUID of the session to process")
    p.add_argument("--all", action="store_true", help="Process all session streams")
    p.add_argument("--consumer", default=f"c-{os.getpid()}", help="Consumer name in the group")
    return p.parse_args()

async def discover_session_ids_from_redis() -> Set[uuid.UUID]:
    # matches "sess:<uuid>:stream"
    ids: Set[uuid.UUID] = set()
    async for key in redis.scan_iter(match="sess:*:stream"):
        try:
            sid = key.split(":")[1]
            ids.add(uuid.UUID(sid))
        except Exception:
            continue
    return ids

async def run_all_sessions(consumer_prefix: str):
    tasks: Dict[uuid.UUID, asyncio.Task] = {}
    while True:
        current = await discover_session_ids_from_redis()
        # start new sessions
        for sid in current:
            if sid not in tasks:
                consumer = f"{consumer_prefix}-{sid}"
                tasks[sid] = asyncio.create_task(worker_loop(sid, consumer))
        # stop sessions that disappeared
        for sid in list(tasks):
            if sid not in current:
                tasks[sid].cancel()
                del tasks[sid]
        await asyncio.sleep(5)  # rescan interval

async def amain():
    args = parse_args()
    if args.all and not args.session_id:
        # one process handles many sessions
        consumer_prefix = args.consumer or socket.gethostname()
        hb_key = "hb:registration_worker:all"
        asyncio.create_task(beat(hb_key))
        await run_all_sessions(consumer_prefix)
        return

    # original single-session mode
    if not args.session_id:
        raise SystemExit("Provide --session-id or use --all")
    session_id = uuid.UUID(args.session_id)
    asyncio.create_task(beat(f"hb:registration_worker:{session_id}"))
    await worker_loop(session_id, args.consumer)

# def parse_args():
#     p = argparse.ArgumentParser(description="Waitlist promotion worker")
#     p.add_argument("--session-id", required=True, help="UUID of the session to process")
#     p.add_argument("--consumer", default=f"c-{os.getpid()}", help="Consumer name in the group")
#     return p.parse_args()


# async def amain():
#     args = parse_args()
#     session_id = uuid.UUID(args.session_id)
#     # start heartbeat (background task)
#     hb_key = f"hb:registration_worker:{session_id}"
#     asyncio.create_task(beat(hb_key))
#     # run the worker loop
#     await worker_loop(session_id, args.consumer)

def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
