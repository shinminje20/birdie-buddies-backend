from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

async def begin_serializable_tx(db: AsyncSession) -> None:
    """
    Ensure we're not inside an active transaction, then start a new one where
    the very first statement is 'SET TRANSACTION ISOLATION LEVEL SERIALIZABLE'.
    """
    # End any auto-begun tx from earlier reads on the same session (safe if none).
    try:
        # SQLAlchemy 2.x exposes in_transaction() on Session/AsyncSession
        if db.in_transaction():
            await db.rollback()
    except Exception:
        # Some adapters behave differently; rollback is safe/no-op if nothing active.
        await db.rollback()

    # This execute will implicitly BEGIN a new tx; SET TRANSACTION is its first statement.
    await db.execute(text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"))
