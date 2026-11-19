"""Format loaders: PDF (text + tables + OCR fallback), DOCX, HTML, Markdown -> ordered Blocks.

No corpus facts live here: only generic structure rules (font size, heading styles, tags).
"""
from __future__ import annotations

import logging
import re
from collections import Counter

from app.ingestion.models import Block

log = logging.getLogger("veltrix.ingestion")

META_FIRST_CELL = re.compile(r"^\s*document\s*id\b", re.I)


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").replace(" ", " ")).strip()


def is_metadata_table(rows: list[list[str]]) -> bool:
    """The document header table (Document ID / Version / Effective date ...) duplicates manifest data."""
    return any(r and META_FIRST_CELL.match(r[0] or "") for r in rows[:2])


def _norm_rows(rows) -> list[list[str]]:
    out = []
    for r in rows or []:
        cells = [_clean(c) for c in r]
        if any(cells):
            out.append(cells)
    return out


# ============================================================ PDF
def load_pdf(path: str) -> tuple[list[Block], list[int]]:
    import pdfplumber

    blocks: list[Block] = []
    ocr_pages: list[int] = []
    with pdfplumber.open(path) as pdf:
        pages_lines = []
        size_counter: Counter = Counter()
        for pno, page in enumerate(pdf.pages, start=1):
            tables = page.find_tables()
            bboxes = [t.bbox for t in tables]
            words = page.extract_words(extra_attrs=["size", "fontname"], use_text_flow=True)
            lines = _group_lines(words, bboxes)
            for ln in lines:
                size_counter[round(ln["size"], 1)] += len(ln["text"])
            items = [("line", ln["top"], ln) for ln in lines]
            for t in tables:
                items.append(("table", t.bbox[1], _norm_rows(t.extract())))
            items.sort(key=lambda x: x[1])
            text_chars = sum(len(ln["text"]) for ln in lines)
            pages_lines.append((pno, items, text_chars, page))

        body_size = size_counter.most_common(1)[0][0] if size_counter else 10.0
        for pno, items, text_chars, page in pages_lines:
            if text_chars < 20:
                from app.ingestion.ocr import ocr_pdf_page
                text = ocr_pdf_page(path, pno - 1)
                ocr_pages.append(pno)
                for para in _paragraphs(text):
                    blocks.append(Block("text", para, page=pno))
                continue
            buf: list[str] = []

            def flush():
                if buf:
                    blocks.append(Block("text", _join_lines(buf), page=pno))
                    buf.clear()

            for kind, _top, item in items:
                if kind == "table":
                    flush()
                    if item and not is_metadata_table(item):
                        blocks.append(Block("table", page=pno, rows=item))
                    continue
                ln = item
                if ln["size"] < body_size * 0.85:        # running header / footer / page number
                    continue
                if ln["bold"] and ln["size"] >= body_size * 1.5:
                    flush()
                    blocks.append(Block("heading", ln["text"], level=0, page=pno))
                elif ln["bold"] and ln["size"] >= body_size * 1.1:
                    flush()
                    blocks.append(Block("heading", ln["text"], level=1, page=pno))
                else:
                    buf.append(ln["text"])
            flush()
    return blocks, ocr_pages


def _group_lines(words: list[dict], bboxes: list[tuple]) -> list[dict]:
    def inside(w):
        cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
        return any(b[0] - 1 <= cx <= b[2] + 1 and b[1] - 1 <= cy <= b[3] + 1 for b in bboxes)

    lines: list[dict] = []
    for w in words:
        if inside(w):
            continue
        if lines and abs(lines[-1]["top"] - w["top"]) < 2.5:
            ln = lines[-1]
            ln["words"].append(w)
        else:
            lines.append({"top": w["top"], "words": [w]})
    out = []
    for ln in lines:
        ws = sorted(ln["words"], key=lambda w: w["x0"])
        text = _clean(" ".join(w["text"] for w in ws))
        if not text:
            continue
        out.append({
            "top": ln["top"], "text": text,
            "size": max(w["size"] for w in ws),
            "bold": all("bold" in (w.get("fontname") or "").lower() for w in ws),
        })
    return out


def _join_lines(lines: list[str]) -> str:
    """Join wrapped lines; keep bullet items on their own lines."""
    out: list[str] = []
    for ln in lines:
        if ln in ("•", "-", "–") :
            out.append("- ")
            continue
        if out and out[-1] == "- ":
            out[-1] = "- " + ln
        elif out and not ln.startswith(("•", "- ")):
            out[-1] = out[-1] + " " + ln
        else:
            out.append(ln.replace("•", "-", 1))
    return "\n".join(out)


def _paragraphs(text: str) -> list[str]:
    paras = [_clean(p) for p in re.split(r"\n\s*\n", text or "")]
    return [p for p in paras if p]


# ============================================================ DOCX
def load_docx(path: str) -> list[Block]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(path)
    blocks: list[Block] = []
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, d)
            text = _clean(p.text)
            if not text:
                continue
            style = (p.style.name if p.style is not None else "") or ""
            m = re.match(r"heading\s*(\d+)", style, re.I)
            if style.lower() == "title":
                blocks.append(Block("heading", text, level=0))
            elif m:
                blocks.append(Block("heading", text, level=int(m.group(1))))
            elif "list" in style.lower():
                blocks.append(Block("text", "- " + text))
            else:
                blocks.append(Block("text", text))
        elif tag == "tbl":
            t = Table(child, d)
            rows = _norm_rows([[c.text for c in r.cells] for r in t.rows])
            if rows and not is_metadata_table(rows):
                blocks.append(Block("table", rows=rows))
    return _merge_list_items(blocks)


def _merge_list_items(blocks: list[Block]) -> list[Block]:
    out: list[Block] = []
    for b in blocks:
        if b.kind == "text" and b.text.startswith("- ") and out and out[-1].kind == "text" and out[-1].text.startswith("- "):
            out[-1].text += "\n" + b.text
        else:
            out.append(b)
    return out


# ============================================================ HTML
def load_html(path: str) -> list[Block]:
    from bs4 import BeautifulSoup

    with open(path, encoding="utf-8", errors="replace") as fh:
        soup = BeautifulSoup(fh.read(), "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "head"]):
        tag.decompose()
    blocks: list[Block] = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table"]):
        if el.find_parent("table") is not None and el.name != "table":
            continue
        if el.name == "table":
            if el.find_parent("table") is not None:
                continue
            rows = _norm_rows([[c.get_text(" ") for c in tr.find_all(["td", "th"])] for tr in el.find_all("tr")])
            if rows and not is_metadata_table(rows):
                blocks.append(Block("table", rows=rows))
        elif el.name[0] == "h":
            text = _clean(el.get_text(" "))
            if text:
                lvl = int(el.name[1])
                blocks.append(Block("heading", text, level=0 if lvl == 1 else lvl - 1))
        else:
            text = _clean(el.get_text(" "))
            if text:
                blocks.append(Block("text", ("- " + text) if el.name == "li" else text))
    return _merge_list_items(blocks)


# ============================================================ Markdown
def load_markdown(path: str) -> list[Block]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    blocks: list[Block] = []
    para: list[str] = []
    table: list[list[str]] = []

    def flush_para():
        if para:
            blocks.append(Block("text", "\n".join(para)))
            para.clear()

    def flush_table():
        if table:
            rows = _norm_rows(table)
            if rows and not is_metadata_table(rows):
                blocks.append(Block("table", rows=rows))
            table.clear()

    for raw in lines:
        line = raw.strip()
        if line.startswith("|"):
            flush_para()
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            table.append(cells)
            continue
        flush_table()
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush_para()
            lvl = len(m.group(1))
            blocks.append(Block("heading", _clean(m.group(2)), level=0 if lvl == 1 else lvl - 1))
        elif not line:
            flush_para()
        elif re.match(r"^[-*+]\s+", line):
            para.append("- " + re.sub(r"^[-*+]\s+", "", line))
        else:
            para.append(line)
    flush_para()
    flush_table()
    return blocks


def load_any(path: str) -> tuple[list[Block], list[int]]:
    ext = path.rsplit(".", 1)[-1].lower()
    if ext == "pdf":
        return load_pdf(path)
    if ext == "docx":
        return load_docx(path), []
    if ext in ("html", "htm"):
        return load_html(path), []
    if ext in ("md", "markdown", "txt"):
        return load_markdown(path), []
    raise ValueError(f"unsupported file type: {ext}")
