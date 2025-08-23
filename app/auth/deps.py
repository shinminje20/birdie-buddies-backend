from __future__ import annotations
from fastapi import Depends, HTTPException, Request
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from ..config import get_settings
from ..db import get_db
from ..models import User
from .jwt import verify_jwt

S = get_settings()


# def _cookie_opts():
#     return {
#         "key": S.SESSION_COOKIE_NAME,
#         "httponly": True,
#         "secure": S.ENV == "prod",
#         "samesite": "lax",
#         "max_age": S.JWT_EXPIRE_MINUTES * 60,
#         "path": "/",
#     }
    
def _cookie_opts():
    return {
        "key": S.SESSION_COOKIE_NAME,
        "httponly": True,
        "secure": True,          # always True in prod (you're behind HTTPS via Caddy)
        "samesite": "none",      # <-- cross-site requires None
        "max_age": S.JWT_EXPIRE_MINUTES * 60,
        "domain":".mybirdies.ca",
        "path": "/",
        # "domain": "api.mybirdies.ca",  # usually omit; host-only is safer
    }


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token: Optional[str] = request.cookies.get(S.SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        claims = verify_jwt(token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive or not found")
    return user
