from __future__ import annotations
import asyncio
import json
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ...redis_client import redis

router = APIRouter(prefix="/events", tags=["events"])

# SSE frame helper
def _sse(data: dict) -> bytes:
    return f"data: {json.dumps(data, separators=(',',':'))}\n\n".encode("utf-8")


async def _stream_pubsub(channel: str) -> AsyncIterator[bytes]:
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    try:
        # initial comment to open stream
        yield b": ok\n\n"
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
            if msg and msg.get("type") == "message":
                payload = msg["data"]
                # if Redis is configured with decode_responses, payload is str; else bytes
                if isinstance(payload, (bytes, bytearray)):
                    payload = payload.decode("utf-8", "ignore")
                try:
                    js = json.loads(payload)
                except Exception:
                    js = {"raw": payload}
                yield _sse(js)
            else:
                # keep-alive comment every few seconds
                yield b": keepalive\n\n"
                await asyncio.sleep(5)
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:
            pass
        await pubsub.close()


@router.get("/sessions/{session_id}")
async def sse_session(session_id: uuid.UUID):
    channel = f"session:{session_id}"
    return StreamingResponse(_stream_pubsub(channel), media_type="text/event-stream")


@router.get("/requests/{request_id}")
async def sse_request(request_id: str):
    channel = f"request:{request_id}"
    return StreamingResponse(_stream_pubsub(channel), media_type="text/event-stream")
