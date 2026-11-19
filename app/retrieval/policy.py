"""Section 5 rules: which documents a requester may use, and which document wins a conflict.

These functions are pure and deterministic. They decide eligibility and precedence from
manifest metadata only; the LLM never decides them.
"""
from __future__ import annotations

from datetime import date

from app.ingestion.models import DocMeta

# Section 3.1: which access levels each role can read.
ROLE_ACCESS: dict[str, frozenset[str]] = {
    "employee": frozenset({"all"}),
    "manager": frozenset({"all", "managers"}),
    "hr": frozenset({"all", "managers", "hr"}),
}

# Section 5.1 authority test: these types outrank faq and wiki.
HIGH_AUTHORITY_TYPES = frozenset({"policy", "addendum", "memo", "handbook", "calendar"})


def can_read(role: str, access_level: str) -> bool:
    return access_level in ROLE_ACCESS.get(role, frozenset())


def region_matches(doc: DocMeta, region: str) -> bool:
    return "GLOBAL" in doc.regions or region.upper() in doc.regions


def is_readable(doc: DocMeta, role: str, region: str) -> bool:
    """Access + region. Restricted documents fail here and are never retrieved."""
    return can_read(role, doc.access_level) and region_matches(doc, region)


def is_effective(doc: DocMeta, ref_date: date) -> bool:
    return date.fromisoformat(doc.effective_date) <= ref_date


def is_eligible(doc: DocMeta, role: str, region: str, ref_date: date) -> bool:
    return is_readable(doc, role, region) and is_effective(doc, ref_date)


def precedence_key(doc: DocMeta) -> tuple[int, int, int]:
    """Lower sorts first = stronger. Authority, then scope, then recency (later date wins)."""
    authority = 0 if doc.doc_type in HIGH_AUTHORITY_TYPES else 1
    scope = 1 if "GLOBAL" in doc.regions else 0
    recency = -date.fromisoformat(doc.effective_date).toordinal()
    return authority, scope, recency


def precedence_ranks(docs: list[DocMeta]) -> dict[str, int]:
    """Dense rank (1 = strongest). Documents with identical keys share a rank: an unresolved tie."""
    keys = sorted({precedence_key(d) for d in docs})
    pos = {k: i + 1 for i, k in enumerate(keys)}
    return {d.doc_id: pos[precedence_key(d)] for d in docs}


def compare(a: DocMeta, b: DocMeta) -> int:
    """-1 if a wins, 1 if b wins, 0 if the tests cannot decide (conflict)."""
    ka, kb = precedence_key(a), precedence_key(b)
    return -1 if ka < kb else (1 if ka > kb else 0)


def deciding_test(a: DocMeta, b: DocMeta) -> str:
    ka, kb = precedence_key(a), precedence_key(b)
    for name, x, y in zip(("authority", "scope", "recency"), ka, kb):
        if x != y:
            return name
    return "tie"
