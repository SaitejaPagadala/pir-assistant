"""Turn raw files into text.  PDFs -> pages (with printed page numbers); HTML -> clean text."""
from __future__ import annotations

from pathlib import Path


def parse_pdf(path: Path, page_offset: int = 0) -> list[dict]:
    import fitz  # PyMuPDF

    pages = []
    with fitz.open(path) as doc:
        for i, page in enumerate(doc):
            pages.append(
                {
                    "pdf_page": i + 1,
                    "page": i + 1 + page_offset,  # printed page number
                    "text": page.get_text("text"),
                }
            )
    return pages


def parse_html(path: Path) -> dict:
    import trafilatura

    html = path.read_text(encoding="utf-8", errors="ignore")
    text = trafilatura.extract(html, include_comments=False, include_tables=True, output_format="txt") or ""
    meta = trafilatura.extract_metadata(html)
    return {
        "title": getattr(meta, "title", None) if meta else None,
        "text": text,
    }
