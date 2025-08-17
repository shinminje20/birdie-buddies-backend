from __future__ import annotations
import argparse
import asyncio
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import EventsOutbox
from ..redis_client import redis

from ..observability.heartbeat import beat


BATCH = 100
SLEEP_EMPTY = 1.0  # seconds
SLEEP_ERROR = 2.0


async def publish_once(db: AsyncSession) -> int:
    # fetch a batch of unsent, ready events and lock them
    rows = await db.execute(
        select(EventsOutbox)
        .where(EventsOutbox.sent_at.is_(None), EventsOutbox.available_at <= sa.func.now())
        .order_by(EventsOutbox.id.asc())
        .limit(BATCH)
        .with_for_update(skip_locked=True)
    )
    events = list(rows.scalars().all())
    if not events:
        await db.rollback()
        return 0

    count = 0
    for evt in events:
        try:
            # publish to Redis Pub/Sub
            await redis.publish(evt.channel, json.dumps(evt.payload))
            evt.sent_at = datetime.now(timezone.utc)
            evt.attempts = (evt.attempts or 0) + 1
            evt.error = None
            count += 1
        except Exception as e:
            evt.attempts = (evt.attempts or 0) + 1
            evt.error = str(e)
            # leave sent_at as NULL so it can be retried later
    await db.commit()
    return count


async def run_forever():
    while True:
        try:
            async with SessionLocal() as db:
                sent = await publish_once(db)
            await asyncio.sleep(SLEEP_EMPTY if sent == 0 else 0.05)
        except Exception:
            await asyncio.sleep(SLEEP_ERROR)


async def amain():
    # start heartbeat as a background task
    asyncio.create_task(beat("hb:outbox_dispatcher:global"))
    # then run the main loop
    await run_forever()

def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
