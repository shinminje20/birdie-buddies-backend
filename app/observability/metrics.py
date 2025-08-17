from __future__ import annotations
import time
from typing import Callable
from fastapi import Response, Request
from ..config import get_settings

S = get_settings()

# Try to import prometheus_client; fall back to no-op metrics if unavailable
PROM_AVAILABLE = True
try:
    from prometheus_client import (
        Counter, Histogram, Gauge, CollectorRegistry,
        CONTENT_TYPE_LATEST, generate_latest
    )
    REGISTRY = CollectorRegistry(auto_describe=True)
except Exception:
    PROM_AVAILABLE = False

    class _NoopMetric:
        def labels(self, *_, **__): return self
        def inc(self, *_, **__): pass
        def observe(self, *_, **__): pass
        def set(self, *_, **__): pass

    def Counter(*_, **__): return _NoopMetric()   # type: ignore
    def Histogram(*_, **__): return _NoopMetric() # type: ignore
    def Gauge(*_, **__): return _NoopMetric()     # type: ignore

    class _NoRegistry: pass
    REGISTRY = _NoRegistry()                       # type: ignore
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

    def generate_latest(_registry):               # type: ignore
        return b""

# ---------- Metric definitions ----------
HTTP_REQS = Counter("http_requests_total", "HTTP requests", ["method", "path", "status"], registry=getattr(REGISTRY, "__class__", None) and REGISTRY)
HTTP_LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency", ["method", "path"], registry=getattr(REGISTRY, "__class__", None) and REGISTRY)

REG_ENQUEUED  = Counter("reg_enqueued_total",  "Registrations enqueued",  ["session_id"], registry=getattr(REGISTRY, "__class__", None) and REGISTRY)
REG_CONFIRMED = Counter("reg_confirmed_total", "Registrations confirmed", ["session_id"], registry=getattr(REGISTRY, "__class__", None) and REGISTRY)
REG_WAITLISTED= Counter("reg_waitlisted_total","Registrations waitlisted",["session_id"], registry=getattr(REGISTRY, "__class__", None) and REGISTRY)
REG_CANCELED  = Counter("reg_canceled_total",  "Registrations canceled",  ["session_id"], registry=getattr(REGISTRY, "__class__", None) and REGISTRY)
PROMOTED      = Counter("reg_promoted_total",  "Registrations promoted",  ["session_id"], registry=getattr(REGISTRY, "__class__", None) and REGISTRY)
SESSIONS_AUTOCLOSED = Counter("sessions_autoclosed_total", "Sessions auto-closed after start", registry=REGISTRY)

# ---------- /metrics endpoint factory ----------
def metrics_app():
    async def _metrics(_: Request):
        if not S.METRICS_ENABLED or not PROM_AVAILABLE:
            return Response(status_code=404)
        data = generate_latest(REGISTRY)
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)
    return _metrics

# ---------- HTTP middleware for latency/counters ----------
class MetricsHTTPMiddleware:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        method = scope["method"]
        path = scope["path"]
        t0 = time.perf_counter()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status = message["status"]
                # These are no-ops when PROM_AVAILABLE is False
                HTTP_REQS.labels(method=method, path=path, status=status).inc()
                HTTP_LATENCY.labels(method=method, path=path).observe(time.perf_counter() - t0)
            await send(message)

        await self.app(scope, receive, send_wrapper)
