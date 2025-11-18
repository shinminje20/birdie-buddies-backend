from __future__ import annotations
import logging
import time
from datetime import datetime, timezone
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from ..observability.logging import get_request_id, bind_record
from ..config import get_settings
from ..auth.jwt import verify_jwt

S = get_settings()
log = logging.getLogger("app.request")

class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = get_request_id(request)
        start = time.perf_counter()
        timestamp = datetime.now(timezone.utc).isoformat()

        # Extract user info from JWT if available
        user_id = None
        user_email = None
        user_name = None
        token = request.cookies.get(S.SESSION_COOKIE_NAME)
        if token:
            try:
                claims = verify_jwt(token)
                user_id = claims.get("sub")
                user_email = claims.get("email")
                user_name = claims.get("name")
            except Exception:
                # Invalid/expired token - user info will remain None
                pass

        # Build user info string for logging
        user_info = f"user_id={user_id or 'anonymous'}"
        if user_name:
            user_info += f" name={user_name}"
        if user_email:
            user_info += f" email={user_email}"

        # response
        try:
            response = await call_next(request)
        except Exception as e:
            dur_ms = int((time.perf_counter() - start) * 1000)
            rec = bind_record(logging.LogRecord(
                name=log.name, level=logging.ERROR, pathname=__file__, lineno=0,
                msg="unhandled_error", args=(), exc_info=None
            ), request_id=rid, extra=f"timestamp={timestamp} path={request.url.path} method={request.method} ms={dur_ms} {user_info}")
            log.handle(rec)
            raise

        dur_ms = int((time.perf_counter() - start) * 1000)
        response.headers[S.REQUEST_ID_HEADER] = rid
        rec = bind_record(logging.LogRecord(
            name=log.name, level=logging.INFO, pathname=__file__, lineno=0,
            msg="request", args=(), exc_info=None
        ), request_id=rid, extra=f"timestamp={timestamp} path={request.url.path} method={request.method} status={response.status_code} ms={dur_ms} {user_info}")
        log.handle(rec)
        return response
