"""Conversation sessions: owner, short history, and at most one waiting write proposal."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class Proposal:
    name: str                 # 'apply_leave' | 'cancel_leave'
    args: dict                # API-visible args (Section 6.1 names)
    summary: dict             # computed details shown to the user


@dataclass
class Session:
    session_id: str
    owner: str
    history: list[tuple[str, str]] = field(default_factory=list)   # (role, text), answers only, no corpus text
    pending: Proposal | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_turn(self, user: str, assistant: str) -> None:
        self.history.append(("user", user))
        self.history.append(("assistant", assistant))
        del self.history[:-12]


class SessionOwnershipError(Exception):
    pass


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str, employee_id: str) -> Session:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                s = Session(session_id, employee_id)
                self._sessions[session_id] = s
            elif s.owner != employee_id:
                raise SessionOwnershipError(session_id)
            return s

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()


SESSIONS = SessionStore()
