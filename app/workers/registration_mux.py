from __future__ import annotations
import asyncio
import json
import os
import uuid
from typing import Dict, List, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
import sqlalchemy as sa
from sqlalchemy import select
from ..db import SessionLocal
from ..models import Session as SessionModel
from ..redis_client import redis
from ..services.registration_allocator import process_registration_request

GROUP = "g1"  # run a single instance of this worker
DISCOVER_EVERY_SEC = 5
BLOCK_MS = 5000

# Keys
def k_stream(session_id: uuid.UUID) -> str:    return f"sess:{session_id}:stream"
def k_req(req_id: str) -> str:                 return f"req:{req_id}:status"
def k_reg2req(reg_id: uuid.UUID) -> str:       return f"regreq:{reg_id}"
def k_backlog(session_id: uuid.UUID) -> str:   return f"sess:{session_id}:backlog"

async def _ensure_group(stream: str) -> None:
    try:
        await redis.xgroup_create(stream, GROUP, id="0", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" in str(e):
            return
        # If stream existed without group, still okay; any other error -> raise
        # (You can also check type)
        if "No such key" in str(e):
            # Race: mkstream=True should have created it; retry once
            await redis.xgroup_create(stream, GROUP, id="$", mkstream=True)
        else:
            raise

async def _discover_session_ids() -> List[uuid.UUID]:
    # Pull currently SCHEDULED sessions; closed/canceled won't accept new registrations
    async with SessionLocal() as db:
        rows = await db.execute(
            select(SessionModel.id).where(SessionModel.status == "scheduled")
        )
        return [r[0] for r in rows.all()]

async def _update_request_status(req_id: str, updates: Dict[str, str]) -> None:
    await redis.hset(k_req(req_id), mapping=updates)

async def _process_msg(session_id: uuid.UUID, msg_id: str, fields: Dict[str, str]) -> None:
    req_id = fields["request_id"]
    user_id = uuid.UUID(fields["user_id"])
    seats = int(fields["seats"])
    guest_names = json.loads(fields.get("guest_names") or "[]")

    async with SessionLocal() as db:  # type: AsyncSession
        state, reg_id, wl_pos = await process_registration_request(
            db,
            request_id=req_id,
            session_id=session_id,
            user_id=user_id,
            seats=seats,
            guest_names=guest_names,
        )

    # Update req status + publish (optional)
    updates: Dict[str, str] = {"state": state}
    if reg_id:
        updates["registration_id"] = str(reg_id)
        await redis.set(k_reg2req(reg_id), req_id, ex=24*60*60)
    if wl_pos is not None:
        updates["waitlist_pos"] = str(wl_pos)
    await _update_request_status(req_id, updates)
    await redis.publish(k_req(req_id), json.dumps(updates))

    # Ack + backlog dec
    await redis.xack(k_stream(session_id), GROUP, msg_id)
    await redis.decr(k_backlog(session_id))

async def main_loop():
    known: Dict[uuid.UUID, str] = {}  # session_id -> stream_key
    consumer = f"c-{os.getpid()}"

    while True:
        # (1) discover/refresh session streams
        try:
            session_ids = await _discover_session_ids()
            for sid in session_ids:
                if sid not in known:
                    stream = k_stream(sid)
                    await _ensure_group(stream)
                    known[sid] = stream
        except Exception:
            # ignore discovery errors briefly
            await asyncio.sleep(1.0)

        if not known:
            await asyncio.sleep(DISCOVER_EVERY_SEC)
            continue

        # (2) build XREADGROUP streams mapping
        streams = {stream: ">" for stream in known.values()}

        try:
            # read one message across all streams (fair enough for tens/hundreds of sessions)
            resp = await redis.xreadgroup(GROUP, consumer, streams=streams, count=1, block=BLOCK_MS)
            if not resp:
                # periodic refresh
                await asyncio.sleep(DISCOVER_EVERY_SEC)
                continue

            # resp = [(stream, [(msg_id, fields_dict), ...])]
            stream, messages = resp[0]
            # find session_id from stream key
            sid = [s for s, k in known.items() if k == stream]
            if not sid:
                # unknown stream; re-discover next loop
                continue
            session_id = sid[0]

            for msg_id, fields in messages:
                try:
                    await _process_msg(session_id, msg_id, fields)
                except Exception:
                    # best-effort: mark rejected
                    req_id = fields.get("request_id")
                    if req_id:
                        await _update_request_status(req_id, {"state": "rejected"})
                    # do NOT ack so it can be retried
                    await asyncio.sleep(0.2)
        except Exception:
            # backoff on Redis issues
            await asyncio.sleep(0.5)

if __name__ == "__main__":
    asyncio.run(main_loop())
