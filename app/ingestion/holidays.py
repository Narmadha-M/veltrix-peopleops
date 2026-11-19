"""Extract holiday dates from calendar documents at ingestion time.

Generic rules only: any table (or OCR text line) in a document whose manifest doc_type is
'calendar' that contains a parseable date becomes a holiday row. A section or column whose label
says 'restricted' / 'optional' / 'floating' marks optional holidays that are NOT office closures.
"""
from __future__ import annotations

import re
from datetime import date

from app.ingestion.models import Block, ParsedDoc

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"], start=1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
            "mon", "tue", "wed", "thu", "fri", "sat", "sun"}
OPTIONAL_RE = re.compile(r"\b(restricted|optional|floating)\b", re.I)

_D_MON_Y = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")
_MON_D_Y = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def parse_date(text: str) -> tuple[date, tuple[int, int]] | None:
    """Return the first unambiguous date in text and its (start, end) span."""
    for rx, order in ((_ISO, "ymd"), (_D_MON_Y, "dmy"), (_MON_D_Y, "mdy")):
        m = rx.search(text or "")
        if not m:
            continue
        try:
            if order == "ymd":
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            elif order == "dmy":
                mon = MONTHS.get(m.group(2).lower())
                if not mon:
                    continue
                d = date(int(m.group(3)), mon, int(m.group(1)))
            else:
                mon = MONTHS.get(m.group(1).lower())
                if not mon:
                    continue
                d = date(int(m.group(3)), mon, int(m.group(2)))
            return d, m.span()
        except ValueError:
            continue
    return None


def _name_from_cells(cells: list[str], date_idx: int, header: list[str] | None) -> str:
    candidates = []
    for i, c in enumerate(cells):
        if i == date_idx or not c or c.strip().lower() in WEEKDAYS:
            continue
        candidates.append((i, c))
    if header:
        for i, c in candidates:
            if i < len(header) and re.search(r"holiday|name|occasion|event|festival", header[i], re.I):
                return c
    return candidates[-1][1] if candidates else "Holiday"


def extract_holidays(doc: ParsedDoc) -> list[tuple[str, str, str, str]]:
    """Rows of (doc_id, ISO date, name, kind)."""
    rows: list[tuple[str, str, str, str]] = []
    section = ""
    found_in_tables = False
    for b in doc.blocks:
        if b.kind == "heading":
            section = b.text
            continue
        if b.kind == "table" and b.rows:
            header = b.rows[0] if parse_date(" ".join(b.rows[0])) is None else None
            label = section + " " + " ".join(header or [])
            kind = "restricted" if OPTIONAL_RE.search(label) else "public"
            for r in (b.rows[1:] if header else b.rows):
                for i, cell in enumerate(r):
                    parsed = parse_date(cell)
                    if parsed:
                        rows.append((doc.meta.doc_id, parsed[0].isoformat(), _name_from_cells(r, i, header), kind))
                        found_in_tables = True
                        break
    if found_in_tables:
        return rows
    # Fallback for calendars without detectable tables (e.g. OCR text): one date per line.
    section = ""
    for b in doc.blocks:
        if b.kind == "heading":
            section = b.text
            continue
        if b.kind != "text":
            continue
        for line in b.text.splitlines():
            if OPTIONAL_RE.search(line) and not parse_date(line):
                section = line
            parsed = parse_date(line)
            if not parsed:
                continue
            d, (s, e) = parsed
            rest = (line[:s] + " " + line[e:]).strip(" -|:,\t")
            name = " ".join(w for w in rest.split() if w.lower().strip(",") not in WEEKDAYS) or "Holiday"
            kind = "restricted" if OPTIONAL_RE.search(section + " " + line) else "public"
            rows.append((doc.meta.doc_id, d.isoformat(), name, kind))
    return rows
