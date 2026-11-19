"""Checks against the real assessment corpus in ./data (skipped when it is not present)."""
import json
import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "data"
pytestmark = pytest.mark.skipif(not (ROOT / "manifest.json").exists(), reason="./data corpus not present")


@pytest.fixture(scope="module")
def parsed():
    from app.ingestion.chunker import chunk_document
    from app.ingestion.loaders import load_any
    from app.ingestion.models import DocMeta, ParsedDoc
    out = {}
    for entry in json.loads((ROOT / "manifest.json").read_text(encoding="utf-8")):
        meta = DocMeta.from_manifest(entry)
        path = ROOT / "corpus" / meta.file
        if not path.exists():
            continue
        blocks, ocr = load_any(str(path))
        doc = ParsedDoc(meta, blocks, ocr)
        out[meta.doc_id] = (doc, chunk_document(doc))
    return out


def test_every_text_document_produces_chunks(parsed):
    empty = [d for d, (doc, chunks) in parsed.items() if not chunks and not doc.ocr_pages]
    assert not empty


def test_chunks_have_locators_and_bounded_size(parsed):
    for doc_id, (_doc, chunks) in parsed.items():
        for c in chunks:
            assert c.locator and c.tokens <= 400, (doc_id, c.locator, c.tokens)


def test_calendar_tables_extract_holidays(parsed):
    from app.ingestion.holidays import extract_holidays
    for doc_id, (doc, _c) in parsed.items():
        if doc.meta.doc_type == "calendar":
            rows = extract_holidays(doc)
            assert any(r[3] == "public" for r in rows), doc_id
            assert all(r[1][:4].isdigit() for r in rows)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_scanned_pdf_is_read_with_ocr(parsed):
    scanned = [(d, doc, ch) for d, (doc, ch) in parsed.items() if doc.ocr_pages]
    assert scanned, "expected at least one scanned page"
    for _d, _doc, chunks in scanned:
        assert sum(len(c.text) for c in chunks) > 200
        assert all(c.locator.startswith("p. ") for c in chunks)
