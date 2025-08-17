from __future__ import annotations
import logging
import sys
import time
import uuid
from pythonjsonlogger import jsonlogger
from fastapi import Request
from ..config import get_settings

S = get_settings()

def setup_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        for h in list(root.handlers):
            root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        "%(levelname)s %(name)s %(message)s %(asctime)s %(request_id)s %(extra)s"
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(S.LOG_LEVEL)

    # quiet noisy loggers if desired
    logging.getLogger("uvicorn.access").setLevel("WARNING")

def get_request_id(req: Request) -> str:
    hdr = S.REQUEST_ID_HEADER
    rid = req.headers.get(hdr)
    return rid if rid else uuid.uuid4().hex

def bind_record(record: logging.LogRecord, **extra):
    # attach arbitrary fields to a log record (safe for missing attrs)
    for k, v in extra.items():
        setattr(record, k, v or "")
    return record
