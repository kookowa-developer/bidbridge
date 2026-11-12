from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import PurePath

from docx import Document
from pypdf import PdfReader

CHUNK_CHARS = 3500


class UnsupportedFormat(ValueError):
    pass


@dataclass(frozen=True)
class Page:
    number: int  # 1-based. For non-paginated sources this is the section number.
    text: str


def _chunk(text: str, size: int = CHUNK_CHARS) -> list[Page]:
    """Split unpaginated text into sections on paragraph boundaries."""
    pages: list[Page] = []
    buf: list[str] = []
    length = 0
    for para in text.splitlines():
        if length + len(para) > size and buf:
            pages.append(Page(len(pages) + 1, "\n".join(buf)))
            buf, length = [], 0
        buf.append(para)
        length += len(para) + 1
    if buf:
        pages.append(Page(len(pages) + 1, "\n".join(buf)))
    return pages or [Page(1, "")]


def parse_document(filename: str, data: bytes) -> list[Page]:
    """Turn an uploaded PDF/DOCX/TXT into numbered pages of plain text."""
    ext = PurePath(filename).suffix.lower()
    if ext == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        return [Page(i + 1, (p.extract_text() or "")) for i, p in enumerate(reader.pages)]
    if ext == ".docx":
        doc = Document(io.BytesIO(data))
        lines = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                lines.append(" | ".join(c.text.strip() for c in row.cells))
        return _chunk("\n".join(lines))
    if ext in {".txt", ".md", ""}:
        text = data.decode("utf-8", errors="replace")
        if "\f" in text:  # form feed = explicit page break
            return [Page(i + 1, t) for i, t in enumerate(text.split("\f"))]
        return _chunk(text)
    raise UnsupportedFormat(f"Qo'llab-quvvatlanmaydigan format: {ext}")


def parse_text(text: str) -> list[Page]:
    return _chunk(text)
