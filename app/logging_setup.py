"""Logging: JSON lines to console and file, with PII redaction on every handler."""
from __future__ import annotations

import json
import logging
import os

from app.config import get_settings
from app.data.pii import RedactingFilter

_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
                   "logger": record.name, "msg": record.getMessage()}
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    s = get_settings()
    root = logging.getLogger("veltrix")
    root.setLevel(s.log_level)
    root.propagate = False
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if s.log_file:
        os.makedirs(os.path.dirname(s.log_file) or ".", exist_ok=True)
        handlers.append(logging.FileHandler(s.log_file, encoding="utf-8"))
    for h in handlers:
        h.setFormatter(JsonFormatter())
        h.addFilter(RedactingFilter())
        root.addHandler(h)
    _configured = True


def log_event(logger: logging.Logger, event: str, **fields) -> None:
    """Structured event. Callers pass IDs, counts and statuses only, never free text from users."""
    logger.info(json.dumps({"event": event, **fields}, default=str))
