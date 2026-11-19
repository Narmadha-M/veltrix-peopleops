"""Live progress events for the streaming endpoint (/chat/stream).

The orchestrator calls emit("searching", ...) at each step. When no listener is set (plain POST /chat)
emit is a no-op, so the assessment endpoint behaves exactly the same.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Callable

_listener: ContextVar[Callable[[str, dict], None] | None] = ContextVar("progress_listener", default=None)


def set_listener(fn: Callable[[str, dict], None] | None):
    return _listener.set(fn)


def emit(stage: str, **data) -> None:
    fn = _listener.get()
    if fn is not None:
        try:
            fn(stage, data)
        except Exception:
            pass  # progress must never break the request
