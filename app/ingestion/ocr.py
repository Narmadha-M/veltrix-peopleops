"""OCR for PDF pages that have no text layer (Tesseract, run locally)."""
from __future__ import annotations

import io
import logging

log = logging.getLogger("veltrix.ingestion")


def ocr_pdf_page(path: str, page_index: int, dpi: int = 300) -> str:
    import pymupdf
    import pytesseract
    from PIL import Image, ImageOps

    with pymupdf.open(path) as doc:
        pix = doc[page_index].get_pixmap(dpi=dpi)
        image = Image.open(io.BytesIO(pix.tobytes("png")))
    image = ImageOps.grayscale(image)
    # light binarisation helps with scanned paper texture
    image = image.point(lambda v: 255 if v > 170 else 0)
    try:
        return pytesseract.image_to_string(image, config="--psm 4")
    except pytesseract.TesseractNotFoundError:
        log.error('{"event": "ocr_unavailable", "detail": "tesseract binary not installed"}')
        return ""
