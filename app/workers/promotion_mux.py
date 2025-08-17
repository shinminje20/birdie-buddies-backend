from __future__ import annotations
import asyncio
import json
import os
import uuid
from typing import Dict, List

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import Session as SessionModel
from ..redis_client import redis
from ..services.waitlist_promotion import promote_waitlist_fifo

GROUP = "g1"
DISCOVER_EVERY_SEC = 5
BLOCK_MS = 5000

def k_promote(session_id: uuid.UUID) -> str:   return f"promote:{session_id}:stream"
def k_req(req_id: str) -> str:                 return f"req:{req_id}:status"
def k_reg2req(reg_id: uuid.UUID) -> str:       return f"regreq:{reg_id}"

async def _ensure_group(stream: str) -> None:
    try:
        await redis.xgroup_create(stream, GROUP, id="$", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise

async def _discover_session_ids() -> List[uuid.UUID]:
    async with SessionLocal() as db:
        rows = await db.execute(select(SessionModel.id).where(SessionModel.status == "scheduled"))
        return [r[0] for r in rows.all()]

async def _set_status_confirmed(reg_id: uuid.UUID) -> None:
    req_id = await redis.get(k_reg2req(reg_id))
    if not req_id:
        return
    updates = {"state": "confirmed", "registration_id": str(reg_id)}
    await redis.hset(k_req(req_id), mapping=updates)
    await redis.publish(k_req(req_id), json.dumps(updates))

async def _handle_message(session_id: uuid.UUID, msg_id: str) -> None:
    async with SessionLocal() as db:  # type: AsyncSession
        promoted = await promote_waitlist_fifo(db, session_id=session_id)
    for reg_id, _seats in promoted:
        await _set_status_confirmed(reg_id)
    await redis.xack(k_promote(session_id), GROUP, msg_id)

async def main_loop():
    known: Dict[uuid.UUID, str] = {}
    consumer = f"c-{os.getpid()}"

    while True:
        try:
            session_ids = await _discover_session_ids()
            for sid in session_ids:
                if sid not in known:
                    stream = k_promote(sid)
                    await _ensure_group(stream)
                    known[sid] = stream
        except Exception:
            await asyncio.sleep(1.0)

        if not known:
            await asyncio.sleep(DISCOVER_EVERY_SEC)
            continue

        streams = {stream: ">" for stream in known.values()}

        try:
            resp = await redis.xreadgroup(GROUP, consumer, streams=streams, count=1, block=BLOCK_MS)
            if not resp:
                await asyncio.sleep(DISCOVER_EVERY_SEC)
                continue

            stream, messages = resp[0]
            sid = [s for s, k in known.items() if k == stream]
            if not sid:
                continue
            session_id = sid[0]
            for msg_id, _fields in messages:
                try:
                    await _handle_message(session_id, msg_id)
                except Exception:
                    # don't ack to retry later
                    await asyncio.sleep(0.2)
        except Exception:
            await asyncio.sleep(0.5)

if __name__ == "__main__":
    asyncio.run(main_loop())
