from __future__ import annotations
from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..models import GmailToken


async def get_active_token(db: AsyncSession) -> Optional[GmailToken]:
    """Get the active Gmail token (single business account)"""
    res = await db.execute(
        select(GmailToken)
        .where(GmailToken.is_active == True)
        .limit(1)
    )
    return res.scalar_one_or_none()


async def upsert_token(
    db: AsyncSession,
    *,
    email: str,
    refresh_token: str,
    history_id: Optional[str] = None,
    watch_expiration: Optional[datetime] = None,
) -> GmailToken:
    """Create or update the Gmail token (single account)"""
    token = await get_active_token(db)

    if token:
        # Update existing token
        token.email = email.lower()
        token.refresh_token = refresh_token
        if history_id is not None:
            token.history_id = history_id
        if watch_expiration is not None:
            token.watch_expiration = watch_expiration
        await db.flush()
        return token

    # Create new token
    token = GmailToken(
        email=email.lower(),
        refresh_token=refresh_token,
        history_id=history_id,
        watch_expiration=watch_expiration,
        is_active=True,
    )
    db.add(token)
    await db.flush()
    return token


async def update_history_id(db: AsyncSession, history_id: str) -> None:
    """Update the last processed history ID"""
    token = await get_active_token(db)
    if token:
        token.history_id = history_id
        await db.flush()


async def update_watch_expiration(db: AsyncSession, expiration: datetime) -> None:
    """Update the watch expiration timestamp"""
    token = await get_active_token(db)
    if token:
        token.watch_expiration = expiration
        await db.flush()
