"""Personal data protection: masking for answers, redaction for logs."""
from __future__ import annotations

import logging
import re
import threading

from app.data import db

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if local and domain else "***"


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


class PIIIndex:
    """Holds the names, emails and phones from the employees table so they can be found in text."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.people: list[dict] = []

    def load(self) -> None:
        with self._lock:
            self.people = db.all_people_pii()

    def ensure(self) -> None:
        if not self.people:
            try:
                self.load()
            except Exception:  # DB not ready yet
                pass

    # -- logs: replace every personal value with the owning employee ID
    def redact_for_logs(self, text: str) -> str:
        self.ensure()
        for p in self.people:
            for value in (p["email"], p["full_name"], p["phone"]):
                if value and value in text:
                    text = text.replace(value, p["employee_id"])
            digits = _digits(p["phone"] or "")
            if len(digits) >= 7 and digits in _digits(text):
                text = _strip_phone_digits(text, digits, p["employee_id"])
        return EMAIL_RE.sub(lambda m: mask_email(m.group(0)), text)

    # -- answers: no phones, emails masked, names only for visible employees
    def sanitize_answer(self, text: str, visible_ids: set[str]) -> str:
        self.ensure()
        for p in self.people:
            if p["phone"]:
                text = text.replace(p["phone"], "[phone number withheld]")
                digits = _digits(p["phone"])
                if len(digits) >= 7 and digits in _digits(text):
                    text = _strip_phone_digits(text, digits, "[phone number withheld]")
            if p["full_name"] and p["employee_id"] not in visible_ids and p["full_name"] in text:
                text = text.replace(p["full_name"], "[name withheld]")
        emails = {p["email"].lower() for p in self.people if p["email"]}
        return EMAIL_RE.sub(
            lambda m: mask_email(m.group(0)) if m.group(0).lower() in emails else m.group(0), text
        )


def _strip_phone_digits(text: str, digits: str, replacement: str) -> str:
    # match the phone digits even when separated by spaces, dashes, dots or brackets
    pattern = r"\+?" + r"[\s\-().]*".join(re.escape(d) for d in digits)
    return re.sub(pattern, replacement, text)


PII = PIIIndex()


class RedactingFilter(logging.Filter):
    """Applied to every log handler: no names, emails or phone numbers reach logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            record.msg = PII.redact_for_logs(message)
            record.args = ()
        except Exception:
            record.msg = "[log message dropped: redaction failed]"
            record.args = ()
        return True
