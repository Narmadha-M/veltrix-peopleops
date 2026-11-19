"""Working days = Mon-Fri dates that are not public holidays of the requester's region.

Holidays come from the calendar document that applies to the requester under Section 5
(eligible on the reference date, highest precedence) and that covers the year in question.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.data import db
from app.ingestion.models import DocMeta
from app.retrieval.policy import is_eligible, precedence_key


def applicable_calendar(docs: dict[str, DocMeta], role: str, region: str, year: int, ref_date: date) -> DocMeta | None:
    candidates = []
    for d in docs.values():
        if d.doc_type != "calendar" or not is_eligible(d, role, region, ref_date):
            continue
        holidays = db.holidays_for_doc(d.doc_id)
        if any(h["holiday_date"].startswith(f"{year}-") for h in holidays):
            candidates.append(d)
    if not candidates:
        return None
    return sorted(candidates, key=precedence_key)[0]


def public_holidays(docs: dict[str, DocMeta], role: str, region: str, year: int, ref_date: date) -> tuple[dict[date, str], DocMeta | None]:
    cal = applicable_calendar(docs, role, region, year, ref_date)
    if cal is None:
        return {}, None
    out = {}
    for h in db.holidays_for_doc(cal.doc_id, kind="public"):
        d = date.fromisoformat(h["holiday_date"])
        if d.year == year:
            out[d] = h["name"]
    return out, cal


def count_working_days(start: date, end: date, holidays: dict[date, str]) -> tuple[int, list[tuple[date, str]]]:
    """Inclusive range. Returns (working days, weekday holidays that were excluded)."""
    if end < start:
        return 0, []
    n, skipped = 0, []
    d = start
    while d <= end:
        if d.weekday() < 5:
            if d in holidays:
                skipped.append((d, holidays[d]))
            else:
                n += 1
        d += timedelta(days=1)
    return n, skipped
