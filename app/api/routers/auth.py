from __future__ import annotations
import secrets
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db import get_db
from ...redis_client import redis
from ...models import User
from ...repos import users as users_repo
from ...auth.jwt import create_jwt
from ...auth.deps import _cookie_opts, get_current_user
from ...services.otp_sender import send_otp_via_email
from fastapi import Request
from ...services.rate_limit import limit_otp_request, limit_otp_verify

router = APIRouter(prefix="/auth", tags=["auth"])

S = get_settings()

OTP_TTL_SECONDS = 5 * 60


class RequestOtpIn(BaseModel):
    email: EmailStr


class VerifyOtpIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)
    # Optional onboarding fields (first-time sign-in)
    name: Optional[str] = None
    phone: Optional[str] = None


class UserOut(BaseModel):
    id: str
    name: str
    email: EmailStr
    phone: Optional[str] = None
    is_admin: bool

    @classmethod
    def from_model(cls, u: User) -> "UserOut":
        return cls(id=str(u.id), name=u.name, email=u.email, phone=u.phone, is_admin=u.is_admin)

def set_session_cookie(response: Response, request: Request, value: str, max_age_seconds: int):
    host = request.url.hostname or ""
    on_localhost = host in {"localhost", "127.0.0.1", "::1"}

    # If you're using HTTPS locally (mkcert/Caddy), flip this to True
    secure_local_https = False

    cookie_kwargs = dict(
        key=S.SESSION_COOKIE_NAME,
        value=value,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=max_age_seconds,
        secure=not on_localhost,
    )

    if not on_localhost:
        cookie_kwargs["domain"] = ".mybirdies.ca"  # only in prod
    
    response.set_cookie(**cookie_kwargs)
    
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response):
    name = S.SESSION_COOKIE_NAME
    expired = "Thu, 01 Jan 1970 00:00:00 GMT"

    def del_cookie(domain: str | None):
        parts = [f"{name}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0", f"Expires={expired}"]
        # Secure can be present or not; deletion works either way
        parts.append("Secure")
        if domain:
            parts.insert(1, f"Domain={domain}")
        response.headers.append("Set-Cookie", "; ".join(parts))

    del_cookie(None)                 # host-only (localhost or api.mybirdies.ca)
    del_cookie(".mybirdies.ca")      # prod domain
    del_cookie("api.mybirdies.ca")

    return Response(status_code=204)


@router.post("/request-otp")
async def request_otp(payload: RequestOtpIn, request: Request):
    await limit_otp_request(request)
    # code = "".join(secrets.choice("0123456789") for _ in range(6))
    code = "123456"
    key = f"otp:{payload.email.lower()}"
    # set new code with TTL; overwrite any previous
    await redis.set(key, code, ex=OTP_TTL_SECONDS)
    await send_otp_via_email(str(payload.email), code)
    return {"sent": True, "ttl_sec": OTP_TTL_SECONDS}


@router.post("/verify-otp", response_model=UserOut)
async def verify_otp(payload: VerifyOtpIn, response: Response, db: AsyncSession = Depends(get_db), request: Request = None):
    await limit_otp_verify(request)
    key = f"otp:{payload.email.lower()}"
    stored = await redis.get(key)
    if not stored or stored != payload.code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired code")

    # Invalidate the OTP immediately
    await redis.delete(key)

    # Upsert user by email (set name/phone if first time or changed)
    user = await users_repo.upsert_by_email(db, email=payload.email, name=payload.name, phone=payload.phone)
    await db.commit()

    # Issue JWT
    token = create_jwt(
        {"sub": str(user.id), "email": user.email, "is_admin": user.is_admin, "name": user.name},
        expires_in=timedelta(minutes=S.JWT_EXPIRE_MINUTES),
    )

    set_session_cookie(response, request, token, max_age_seconds=S.JWT_EXPIRE_MINUTES * 60)
    return UserOut.from_model(user)


@router.get("/me", response_model=UserOut)
async def me(current: User = Depends(get_current_user)) -> UserOut:
    return UserOut.from_model(current)
