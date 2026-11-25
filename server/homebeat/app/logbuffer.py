"""In-memory ring buffer of recent log records for the admin console (GET /admin/logs, SSE tail)."""
from __future__ import annotations

import collections
import logging
import re
import threading
from datetime import UTC, datetime

MAX_RECORDS = 2000
LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}
_TOKEN_RE = re.compile(r"([?&]token=)[^&\s\"]+")
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]+")


class RingBuffer(logging.Handler):
    def __init__(self, capacity: int = MAX_RECORDS):
        super().__init__(level=logging.DEBUG)
        self.records: collections.deque[dict] = collections.deque(maxlen=capacity)
        self.seq = 0
        self._lock2 = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                msg = f"{msg}\n{record.exc_text}"
        except Exception:  # never let logging break the app
            msg = str(record.msg)
        msg = _BEARER_RE.sub(r"\1REDACTED", _TOKEN_RE.sub(r"\1REDACTED", msg))[:8000]
        with self._lock2:
            self.seq += 1
            self.records.append({
                "seq": self.seq,
                "at": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "level": record.levelname, "levelno": record.levelno, "logger": record.name, "message": msg,
            })

    def since(self, after: int | None = None, level: str = "INFO", limit: int = MAX_RECORDS) -> list[dict]:
        minimum = LEVELS.get(level, logging.INFO)
        with self._lock2:
            items = [r for r in self.records if (after is None or r["seq"] > after) and r["levelno"] >= minimum]
        return [{k: v for k, v in r.items() if k != "levelno"} for r in items[-limit:]]


BUFFER = RingBuffer()


def install() -> RingBuffer:
    root = logging.getLogger()
    if BUFFER not in root.handlers:
        root.addHandler(BUFFER)
    return BUFFER
