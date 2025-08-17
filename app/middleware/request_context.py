from __future__ import annotations
import logging
import time
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from ..observability.logging import get_request_id, bind_record
from ..config import get_settings

S = get_settings()
log = logging.getLogger("app.request")

class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = get_request_id(request)
        start = time.perf_counter()

        # response
        try:
            response = await call_next(request)
        except Exception as e:
            dur_ms = int((time.perf_counter() - start) * 1000)
            rec = bind_record(logging.LogRecord(
                name=log.name, level=logging.ERROR, pathname=__file__, lineno=0,
                msg="unhandled_error", args=(), exc_info=None
            ), request_id=rid, extra=f"path={request.url.path} method={request.method} ms={dur_ms}")
            log.handle(rec)
            raise

        dur_ms = int((time.perf_counter() - start) * 1000)
        response.headers[S.REQUEST_ID_HEADER] = rid
        rec = bind_record(logging.LogRecord(
            name=log.name, level=logging.INFO, pathname=__file__, lineno=0,
            msg="request", args=(), exc_info=None
        ), request_id=rid, extra=f"path={request.url.path} method={request.method} status={response.status_code} ms={dur_ms}")
        log.handle(rec)
        return response
