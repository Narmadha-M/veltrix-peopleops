"""Deterministic date helpers given APP_TODAY (weeks start Monday)."""
from __future__ import annotations

from datetime import date, timedelta

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def next_weekday(today: date, weekday: int) -> date:
    """First given weekday strictly after today ('next Friday')."""
    delta = (weekday - today.weekday()) % 7
    return today + timedelta(days=delta or 7)


def next_week(today: date) -> tuple[date, date]:
    monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
    return monday, monday + timedelta(days=4)


def this_week(today: date) -> tuple[date, date]:
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=4)


def date_hints(today: date) -> str:
    nw = next_week(today)
    tw = this_week(today)
    lines = [
        f"today: {today.isoformat()} ({WEEKDAY_NAMES[today.weekday()]})",
        f"tomorrow: {(today + timedelta(days=1)).isoformat()} ({WEEKDAY_NAMES[(today.weekday() + 1) % 7]})",
        f"this week (Mon-Fri): {tw[0].isoformat()} to {tw[1].isoformat()}",
        f"next week (Mon-Fri): {nw[0].isoformat()} to {nw[1].isoformat()}",
    ]
    for i, name in enumerate(WEEKDAY_NAMES):
        lines.append(f"next {name}: {next_weekday(today, i).isoformat()}")
    return "\n".join(lines)
