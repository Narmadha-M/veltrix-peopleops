"""Ingestion pipeline: manifest + corpus -> chunks, search index and holiday table. Fully automatic."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field

from app.config import get_settings
from app.data import db
from app.data.pii import PII
from app.ingestion.chunker import chunk_document
from app.ingestion.holidays import extract_holidays
from app.ingestion.loaders import load_any
from app.ingestion.models import Chunk, DocMeta, ParsedDoc
from app.logging_setup import log_event

log = logging.getLogger("veltrix.ingestion")


@dataclass
class KnowledgeBase:
    docs: dict[str, DocMeta] = field(default_factory=dict)
    chunks: list[Chunk] = field(default_factory=list)
    index: object | None = None
    ready: bool = False
    error: str | None = None


KB = KnowledgeBase()
_lock = threading.Lock()


def load_manifest(path: str) -> dict[str, DocMeta]:
    with open(path, encoding="utf-8") as fh:
        entries = json.load(fh)
    return {e["doc_id"]: DocMeta.from_manifest(e) for e in entries}


def ingest(kb: KnowledgeBase = KB) -> KnowledgeBase:
    from app.retrieval.index import Embedder, SearchIndex

    s = get_settings()
    started = time.time()
    with _lock:
        kb.ready = False
        db.ensure_extra_tables()
        PII.load()
        docs = load_manifest(s.manifest_path)
        chunks: list[Chunk] = []
        holiday_rows: list[tuple[str, str, str, str]] = []
        for meta in docs.values():
            path = os.path.join(s.corpus_dir, meta.file)
            if not os.path.exists(path):
                log_event(log, "ingest_missing_file", doc_id=meta.doc_id)
                continue
            try:
                blocks, ocr_pages = load_any(path)
            except Exception as exc:  # one bad file must not stop the service
                log_event(log, "ingest_failed", doc_id=meta.doc_id, error=type(exc).__name__)
                continue
            parsed = ParsedDoc(meta=meta, blocks=blocks, ocr_pages=ocr_pages)
            doc_chunks = chunk_document(parsed)
            chunks.extend(doc_chunks)
            if meta.doc_type == "calendar":
                holiday_rows.extend(extract_holidays(parsed))
            log_event(log, "ingest_doc", doc_id=meta.doc_id, blocks=len(blocks), chunks=len(doc_chunks),
                      ocr_pages=ocr_pages, tokens=sum(c.tokens for c in doc_chunks))
        db.replace_holidays(holiday_rows)

        embedder = None
        if s.embeddings == "fastembed":
            try:
                embedder = Embedder(s.embedding_model, s.model_cache_dir)
            except Exception as exc:
                log_event(log, "embeddings_unavailable", error=type(exc).__name__, fallback="bm25_only")
        kb.docs, kb.chunks = docs, chunks
        kb.index = SearchIndex(chunks, embedder)
        kb.ready, kb.error = True, None
        log_event(log, "ingest_done", docs=len(docs), chunks=len(chunks), holidays=len(holiday_rows),
                  dense=embedder is not None, seconds=round(time.time() - started, 1))
    return kb


def ingest_in_background() -> threading.Thread:
    def run():
        try:
            ingest()
        except Exception as exc:
            KB.error = type(exc).__name__
            log.exception('{"event": "ingest_crashed"}')

    t = threading.Thread(target=run, name="ingestion", daemon=True)
    t.start()
    return t
