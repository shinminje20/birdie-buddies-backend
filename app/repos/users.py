from __future__ import annotations
import uuid
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..models import User
from pydantic import EmailStr


async def get_by_id(db: AsyncSession, user_id: uuid.UUID) -> Optional[User]:
    res = await db.execute(select(User).where(User.id == user_id))
    return res.scalar_one_or_none()


async def get_by_email(db: AsyncSession, email: EmailStr) -> Optional[User]:
    res = await db.execute(select(User).where(User.email == str(email)))
    return res.scalar_one_or_none()


async def upsert_by_email(
    db: AsyncSession,
    *,
    email: EmailStr,
    name: Optional[str] = None,
    phone: Optional[str] = None,
) -> User:
    user = await get_by_email(db, email)
    if user:
        changed = False
        if name and user.name != name:
            user.name = name
            changed = True
        if phone and user.phone != phone:
            user.phone = phone
            changed = True
        if changed:
            await db.flush()
        return user

    user = User(name=name or "Player", email=str(email), phone=phone or None)
    db.add(user)
    await db.flush()
    return user
