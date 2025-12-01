from __future__ import annotations
import logging
import argparse
import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Tuple, Set

import asyncio, socket

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import SessionLocal
from ..redis_client import redis
from ..services.registration_allocator import process_registration_request
from ..observability.heartbeat import beat

log = logging.getLogger(__name__)
S = get_settings()

# Keys
def k_stream(session_id: uuid.UUID) -> str:   return f"sess:{session_id}:stream"
def k_req(req_id: str) -> str:                return f"req:{req_id}:status"
def k_reg2req(reg_id: uuid.UUID) -> str:     return f"regreq:{reg_id}"
def k_backlog(session_id: uuid.UUID) -> str: return f"sess:{session_id}:backlog"

REQ_TTL_SEC = 24 * 60 * 60

# Consumer group defaults
GROUP = "g1"


async def _ensure_group(stream: str, group: str) -> None:
    try:
        await redis.xgroup_create(stream, group, id="$", mkstream=True)
    except Exception as e:
        # BUSYGROUP means it already exists; ignore
        if "BUSYGROUP" in str(e):
            return
        raise


async def _update_request_status(req_id: str, updates: Dict[str, Any]) -> None:
    req_key = k_req(req_id)
    # Keep a TTL so status is queryable for a day
    await redis.hset(req_key, mapping=updates)
    await redis.expire(req_key, REQ_TTL_SEC)


async def handle_message(session_id: uuid.UUID, msg_id: str, fields: Dict[str, str]) -> None:
    """
    fields:
      request_id, user_id, seats, guest_names(json), idempotency_key, ts
    """
    req_id = fields["request_id"]
    user_id = uuid.UUID(fields["user_id"])
    seats = int(fields["seats"])
    guest_names = json.loads(fields.get("guest_names") or "[]")

    # Process in DB
    async with SessionLocal() as db:  # type: AsyncSession
        state, reg_id, wl_pos, reg_ids = await process_registration_request(
            db,
            request_id=req_id,
            session_id=session_id,
            user_id=user_id,
            seats=seats,
            guest_names=guest_names,
        )

    # Update status hash
    updates: Dict[str, Any] = {"state": state}
    # map all created registrations -> request for future status updates (promotion)
    for rid in reg_ids:
        await redis.set(k_reg2req(rid), req_id, ex=REQ_TTL_SEC)
    if reg_id:
        updates["registration_id"] = str(reg_id)
    if wl_pos is not None:
        updates["waitlist_pos"] = str(wl_pos)
    await _update_request_status(req_id, updates)
    
    # publish request status live
    await redis.publish(k_req(req_id), json.dumps({"state": state, "registration_id": str(reg_id) if reg_id else None, "waitlist_pos": wl_pos}))

    # Ack the message
    await redis.xack(k_stream(session_id), GROUP, msg_id)
    # Optionally trim/del to keep stream small:
    # await redis.xdel(k_stream(session_id), msg_id)

    # Decrement backlog counter
    await redis.decr(k_backlog(session_id))


async def worker_loop(session_id: uuid.UUID, consumer: str) -> None:
    stream = k_stream(session_id)
    await _ensure_group(stream, GROUP)

    while True:
        resp = await redis.xreadgroup(GROUP, consumer, streams={stream: ">"}, count=1, block=5000)
        if not resp:
            continue
        # resp like: [(stream, [(msg_id, {field: val, ...})])]
        _, messages = resp[0]
        for msg_id, fields in messages:
            try:
                await handle_message(session_id, msg_id, fields)
            except Exception as e:
                # On failure, do not ack. Optionally put error info into status
                log.error(f" Error while worker loop : {e}")
                await _update_request_status(fields["request_id"], {"state": "rejected"})
                # small delay to avoid tight loop
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
#     p = argparse.ArgumentParser(description="Registration allocation worker")
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
