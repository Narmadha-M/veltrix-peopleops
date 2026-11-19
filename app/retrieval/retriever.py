"""Retrieval with access control BEFORE scoring, precedence labelling and the corpus token budget."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from app.config import get_settings
from app.ingestion.models import Chunk, DocMeta
from app.ingestion.pipeline import KB, KnowledgeBase
from app.logging_setup import log_event
from app.retrieval.budget import count_tokens
from app.retrieval.policy import is_effective, is_readable, precedence_ranks

log = logging.getLogger("veltrix.retrieval")

CURRENT_TOP_K = 8
UPCOMING_TOP_K = 3
UPCOMING_RESERVE = 600


@dataclass
class Evidence:
    eid: str
    chunk: Chunk
    doc: DocMeta
    rank: int
    upcoming: bool

    def render(self) -> str:
        status = f"UPCOMING (not yet in force; effective {self.doc.effective_date})" if self.upcoming else "IN FORCE"
        return (f"<evidence id=\"{self.eid}\" doc_id=\"{self.doc.doc_id}\" type=\"{self.doc.doc_type}\" "
                f"regions=\"{','.join(self.doc.regions)}\" effective=\"{self.doc.effective_date}\" "
                f"precedence_rank=\"{self.rank}\" status=\"{status}\">\n{self.chunk.text}\n</evidence>")


@dataclass
class EvidenceSet:
    items: list[Evidence]
    corpus_tokens: int
    reference_date: date

    def by_id(self) -> dict[str, Evidence]:
        return {e.eid: e for e in self.items}

    def render(self) -> str:
        return "\n\n".join(e.render() for e in self.items)


def readable_doc_ids(kb: KnowledgeBase, role: str, region: str) -> set[str]:
    return {d.doc_id for d in kb.docs.values() if is_readable(d, role, region)}


def retrieve(query: str, role: str, region: str, ref_date: date, kb: KnowledgeBase = KB,
             budget: int | None = None) -> EvidenceSet:
    budget = budget or get_settings().corpus_token_budget
    readable = readable_doc_ids(kb, role, region)
    # Access control: only chunks of readable documents are even candidates for scoring.
    allowed = [i for i, c in enumerate(kb.chunks) if c.doc_id in readable]
    hits = kb.index.search(query, allowed, top_k=CURRENT_TOP_K + UPCOMING_TOP_K + 6) if kb.index else []

    current, upcoming = [], []
    for pos, _score in hits:
        chunk = kb.chunks[pos]
        doc = kb.docs[chunk.doc_id]
        (current if is_effective(doc, ref_date) else upcoming).append((chunk, doc))
    current, upcoming = current[:CURRENT_TOP_K], upcoming[:UPCOMING_TOP_K]

    ranks = precedence_ranks([d for _, d in current + upcoming])
    items: list[Evidence] = []
    used = 0
    reserve = UPCOMING_RESERVE if upcoming else 0

    def try_add(chunk, doc, is_upcoming, limit):
        nonlocal used
        ev = Evidence(f"E{len(items) + 1}", chunk, doc, ranks[doc.doc_id], is_upcoming)
        n = count_tokens(ev.render())
        if used + n <= limit:
            items.append(ev)
            used += n

    for chunk, doc in current:
        try_add(chunk, doc, False, budget - reserve)
    for chunk, doc in upcoming:
        try_add(chunk, doc, True, budget)

    total = count_tokens("\n\n".join(e.render() for e in items))
    assert total <= budget, "corpus token budget exceeded"
    log_event(log, "retrieval", role=role, region=region, ref_date=ref_date.isoformat(),
              candidates=len(allowed), hits=len(hits), evidence=[e.chunk.chunk_id for e in items],
              corpus_tokens=total)
    return EvidenceSet(items, total, ref_date)
