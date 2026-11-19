"""Heading-aware chunking. Each chunk = one section (or part of a long one) with a citation locator."""
from __future__ import annotations

from app.ingestion.models import Block, Chunk, ParsedDoc
from app.retrieval.budget import count_tokens

MAX_CHUNK_TOKENS = 350


def render_table(rows: list[list[str]]) -> list[str]:
    """Each row becomes a self-describing line ('Header: value | Header: value') so rows survive splitting."""
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    if not body:
        return [" | ".join(header)]
    lines = []
    for r in body:
        cells = [f"{h}: {v}" if h else v for h, v in zip(header, r) if v]
        lines.append("- " + " | ".join(cells))
    return lines


def _sections(doc: ParsedDoc) -> tuple[str, list[tuple[str, list[str]]]]:
    blocks = doc.blocks
    title = next((b.text for b in blocks if b.kind == "heading" and b.level == 0), doc.meta.title)
    has_sections = any(b.kind == "heading" and b.level >= 1 for b in blocks)
    sections: list[tuple[str, list[str]]] = []

    def current(locator: str) -> list[str]:
        if not sections or sections[-1][0] != locator:
            sections.append((locator, []))
        return sections[-1][1]

    locator = None
    for b in blocks:
        if b.kind == "heading":
            if b.level >= 1:
                locator = b.text
                current(locator)
            continue
        if has_sections:
            loc = locator or (f"p. {b.page}" if b.page else title)
        else:
            loc = f"p. {b.page}" if b.page else title
        target = current(loc)
        if b.kind == "table":
            target.extend(render_table(b.rows))
        elif b.text:
            target.append(b.text)
    return title, [(loc, parts) for loc, parts in sections if parts]


def chunk_document(doc: ParsedDoc) -> list[Chunk]:
    title, sections = _sections(doc)
    chunks: list[Chunk] = []
    for locator, parts in sections:
        header = f"[{title}] {locator}"
        buf: list[str] = []
        for part in _split_long(parts):
            candidate = "\n".join([header, *buf, part])
            if buf and count_tokens(candidate) > MAX_CHUNK_TOKENS:
                chunks.append(_mk(doc, len(chunks), locator, "\n".join([header, *buf])))
                buf = []
            buf.append(part)
        if buf:
            chunks.append(_mk(doc, len(chunks), locator, "\n".join([header, *buf])))
    return chunks


def _split_long(parts: list[str]) -> list[str]:
    """Split any single paragraph that is itself longer than the chunk size, at sentence boundaries."""
    out: list[str] = []
    for p in parts:
        if count_tokens(p) <= MAX_CHUNK_TOKENS - 40:
            out.append(p)
            continue
        sentence_buf = ""
        for sentence in p.replace("\n", " ").split(". "):
            piece = (sentence_buf + ". " + sentence) if sentence_buf else sentence
            if count_tokens(piece) > MAX_CHUNK_TOKENS - 40 and sentence_buf:
                out.append(sentence_buf.strip() + ".")
                sentence_buf = sentence
            else:
                sentence_buf = piece
        if sentence_buf:
            out.append(sentence_buf.strip())
    return out


def _mk(doc: ParsedDoc, n: int, locator: str, text: str) -> Chunk:
    return Chunk(chunk_id=f"{doc.meta.doc_id}#{n}", doc_id=doc.meta.doc_id, locator=locator,
                 text=text, tokens=count_tokens(text))
