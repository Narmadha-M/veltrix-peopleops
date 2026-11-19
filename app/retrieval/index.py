"""In-process search index: BM25 (keyword) + optional dense embeddings, fused with RRF.

The corpus is small (tens of documents), so exact search over a NumPy matrix is used instead of an
approximate vector index; see DESIGN.md for how this changes at 10,000 documents.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter

import numpy as np

from app.ingestion.models import Chunk

log = logging.getLogger("veltrix.retrieval")

STOPWORDS = frozenset(
    "a an and are as at be by can do does for from get got have how i if in is it its many me much my "
    "of on or our should so than that the their there these this to was we what when where which who "
    "will with would you your".split()
)


def tokenize(text: str) -> list[str]:
    out = []
    for t in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if t in STOPWORDS:
            continue
        if len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.docs = docs
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = (sum(self.len) / len(docs)) if docs else 0.0
        df: Counter = Counter()
        for d in docs:
            df.update(set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str], allowed: list[int]) -> dict[int, float]:
        out = {}
        for i in allowed:
            tf, dl, s = self.tf[i], self.len[i], 0.0
            for t in query:
                f = tf.get(t)
                if not f:
                    continue
                s += self.idf.get(t, 0.0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / (self.avg or 1)))
            if s > 0:
                out[i] = s
        return out


class Embedder:
    def __init__(self, model_name: str, cache_dir: str) -> None:
        from fastembed import TextEmbedding
        self.model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = np.array(list(self.model.embed(texts)), dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.maximum(norms, 1e-9)


class SearchIndex:
    def __init__(self, chunks: list[Chunk], embedder: Embedder | None) -> None:
        self.chunks = chunks
        self.bm25 = BM25([tokenize(c.text) for c in chunks])
        self.embedder = embedder
        self.vectors = embedder.embed([c.text for c in chunks]) if (embedder and chunks) else None

    def search(self, query: str, allowed: list[int], top_k: int) -> list[tuple[int, float]]:
        """Score ONLY the allowed chunk positions. Returns (position, fused score), best first."""
        if not allowed:
            return []
        rrf: dict[int, float] = {}
        bm = self.bm25.scores(tokenize(query), allowed)
        for rank, (i, _s) in enumerate(sorted(bm.items(), key=lambda x: (-x[1], x[0]))):
            rrf[i] = rrf.get(i, 0.0) + 1.0 / (60 + rank)
        if self.vectors is not None:
            q = self.embedder.embed([query])[0]
            sims = self.vectors[allowed] @ q
            order = np.argsort(-sims, kind="stable")
            for rank, j in enumerate(order[: max(top_k * 3, 20)]):
                if sims[j] < 0.35:
                    break
                i = allowed[int(j)]
                rrf[i] = rrf.get(i, 0.0) + 1.0 / (60 + rank)
        return sorted(rrf.items(), key=lambda x: (-x[1], x[0]))[:top_k]
