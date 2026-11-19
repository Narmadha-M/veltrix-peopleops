from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DocMeta:
    doc_id: str
    file: str
    title: str
    doc_type: str
    regions: list[str]
    effective_date: str
    access_level: str

    @classmethod
    def from_manifest(cls, entry: dict) -> "DocMeta":
        return cls(
            doc_id=entry["doc_id"], file=entry["file"], title=entry.get("title", entry["doc_id"]),
            doc_type=str(entry.get("doc_type", "")).lower(),
            regions=[str(r).upper() for r in entry.get("regions", ["GLOBAL"])],
            effective_date=entry["effective_date"], access_level=str(entry.get("access_level", "all")).lower(),
        )


@dataclass
class Block:
    """One structural element of a document, in reading order."""
    kind: str                     # 'heading' | 'text' | 'table'
    text: str = ""
    level: int = 1                # heading level (0 = document title)
    page: int | None = None
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class ParsedDoc:
    meta: DocMeta
    blocks: list[Block]
    ocr_pages: list[int] = field(default_factory=list)


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    locator: str
    text: str                     # text sent to the LLM (includes title + locator line)
    tokens: int
