from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from ..models import EventsOutbox

async def add_outbox_event(db: AsyncSession, *, channel: str, payload: dict) -> EventsOutbox:
    evt = EventsOutbox(channel=channel, payload=payload)
    db.add(evt)
    # no commit here; caller’s transaction should commit
    return evt
