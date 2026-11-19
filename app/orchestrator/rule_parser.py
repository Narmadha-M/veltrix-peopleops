"""Rule-based understanding of leave write requests, used in keyword mode (no LLM).

It only extracts arguments (leave type, dates, request ID). Every business rule, permission check and the
confirmation step still run in code exactly as in LLM mode, so a wrong parse can never write data unseen:
the user always sees the computed proposal and must reply "yes".
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from app.orchestrator.dates import WEEKDAY_NAMES, next_week, next_weekday, this_week

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?" \
       r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s*(\d{4}))?"
_SEP = r"\s*(?:-|–|to|till|until|through|thru)\s*"

_APPLY = re.compile(r"\b(apply|book|take|request|need|want|put in|submit)\b", re.I)
_CANCEL = re.compile(r"\b(cancel|withdraw|revoke|delete|remove)\b", re.I)
_LEAVE_WORD = re.compile(r"\b(leave|off|holiday|vacation|pto|sick)\b", re.I)
_REQUEST_ID = re.compile(r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b", re.I)
_QUESTION = re.compile(r"^\s*(how|what|when|which|who|why|is|are|can|could|may|do|does|should|will|am)\b", re.I)


def _month(token: str) -> int:
    return _MONTHS[token[:3].lower()]


def _make(day: int, month: int, year: int | None, today: date) -> date | None:
    try:
        d = date(year or today.year, month, day)
    except ValueError:
        return None
    if year is None and d < today - timedelta(days=31):
        try:
            d = date(today.year + 1, month, day)        # "14 Jan" asked in November means next January
        except ValueError:
            return None
    return d


def _single_dates(text: str, today: date) -> list[tuple[int, date]]:
    """All single dates in the text with their position."""
    found: list[tuple[int, date]] = []
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        try:
            found.append((m.start(), date(int(m[1]), int(m[2]), int(m[3]))))
        except ValueError:
            pass
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b", text):     # dd/mm[/yyyy]
        d = _make(int(m[1]), int(m[2]), int(m[3]) if m[3] else None, today)
        if d:
            found.append((m.start(), d))
    for m in re.finditer(rf"\b{_DAY}\s*(?:of\s+)?{_MON}\b{_YEAR}", text, re.I):          # 14 Dec
        d = _make(int(m[1]), _month(m[2]), int(m[3]) if m[3] else None, today)
        if d:
            found.append((m.start(), d))
    for m in re.finditer(rf"\b{_MON}\s+{_DAY}\b{_YEAR}", text, re.I):                    # Dec 14
        d = _make(int(m[2]), _month(m[1]), int(m[3]) if m[3] else None, today)
        if d:
            found.append((m.start(), d))
    low = text.lower()
    for word, offset in (("today", 0), ("tomorrow", 1), ("day after tomorrow", 2)):
        i = low.find(word)
        if i >= 0 and not (word == "tomorrow" and "day after tomorrow" in low):
            found.append((i, today + timedelta(days=offset)))
    for i, name in enumerate(WEEKDAY_NAMES):
        m = re.search(rf"\b(?:next|this|on|coming)?\s*{name}\b", text, re.I)
        if m:
            found.append((m.start(), next_weekday(today, i)))
    return sorted(set(found), key=lambda x: x[0])


def parse_date_range(text: str, today: date) -> tuple[str, str] | None:
    """Returns (start, end) as ISO dates, or None if no date is mentioned."""
    # "14-18 Dec", "14 to 18 December 2026"
    m = re.search(rf"\b{_DAY}{_SEP}{_DAY}\s*(?:of\s+)?{_MON}\b{_YEAR}", text, re.I)
    if m:
        year = int(m[4]) if m[4] else None
        a, b = _make(int(m[1]), _month(m[3]), year, today), _make(int(m[2]), _month(m[3]), year, today)
        if a and b:
            return a.isoformat(), b.isoformat()
    # "Dec 14-18"
    m = re.search(rf"\b{_MON}\s+{_DAY}{_SEP}{_DAY}\b{_YEAR}", text, re.I)
    if m:
        year = int(m[4]) if m[4] else None
        a, b = _make(int(m[2]), _month(m[1]), year, today), _make(int(m[3]), _month(m[1]), year, today)
        if a and b:
            return a.isoformat(), b.isoformat()
    low = text.lower()
    if "next week" in low:
        a, b = next_week(today)
        return a.isoformat(), b.isoformat()
    if "this week" in low:
        a, b = this_week(today)
        return max(a, today).isoformat(), b.isoformat()
    dates = _single_dates(text, today)
    if not dates:
        return None
    start = dates[0][1]
    end = dates[1][1] if len(dates) > 1 else start
    if end < start and len(dates) > 1 and end.year == start.year:
        end = end.replace(year=end.year + 1) if (start - end).days > 31 else end
    return start.isoformat(), end.isoformat()


def leave_type_of(text: str) -> str | None:
    if re.search(r"\b(sick|ill|unwell|medical|doctor|fever)\b", text, re.I):
        return "sick"
    if re.search(r"\b(annual|vacation|holiday|pto|casual|earned|privilege|trip|travel)\b", text, re.I):
        return "annual"
    return None


def parse_write(message: str, today: date) -> dict | None:
    """A tool call dict like the LLM planner produces ({'name': 'apply_leave', ...}), or None."""
    if not _LEAVE_WORD.search(message) and not _REQUEST_ID.search(message):
        return None
    rng = parse_date_range(message, today)
    if not rng and not _REQUEST_ID.search(message) and (_QUESTION.search(message) or "?" in message):
        return None                      # a policy question such as "can I take sick leave?", not a request
    if _CANCEL.search(message):
        call: dict = {"name": "cancel_leave"}
        rid = _REQUEST_ID.search(message)
        if rid:
            call["request_id"] = rid[1].lower()
        if rng:
            call["start_date"], call["end_date"] = rng
        if lt := leave_type_of(message):
            call["leave_type"] = lt
        return call
    if _APPLY.search(message) or (rng and _LEAVE_WORD.search(message) and not message.rstrip().endswith("?")):
        call = {"name": "apply_leave"}
        if lt := leave_type_of(message):
            call["leave_type"] = lt
        if rng:
            call["start_date"], call["end_date"] = rng
        return call
    return None
