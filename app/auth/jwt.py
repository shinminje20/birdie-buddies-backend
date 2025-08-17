from __future__ import annotations
import time
from datetime import timedelta
from typing import Any, Dict
import jwt  # PyJWT

from ..config import get_settings

S = get_settings()

ALGO = "HS256"


def _now() -> int:
    return int(time.time())


def create_jwt(payload: Dict[str, Any], expires_in: timedelta) -> str:
    iat = _now()
    exp = iat + int(expires_in.total_seconds())
    to_encode = {
        "iss": S.APP_NAME,
        "aud": S.APP_NAME,
        "iat": iat,
        "exp": exp,
        **payload,
    }
    return jwt.encode(to_encode, S.JWT_SECRET, algorithm=ALGO)


def verify_jwt(token: str) -> Dict[str, Any]:
    return jwt.decode(
        token,
        S.JWT_SECRET,
        algorithms=[ALGO],
        audience=S.APP_NAME,
        issuer=S.APP_NAME,
    )
