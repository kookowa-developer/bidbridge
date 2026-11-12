"""Shared ingest -> OCR -> extract -> persist pipeline (used by the HTTP API and the Telegram bot)."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import PurePath

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Company, Requirement, Tender
from app.services import extractor
from app.services.ingest import Page, parse_document, parse_text
from app.services.ocr import IMAGE_EXT, MIN_TEXT_CHARS, OCRProvider

log = logging.getLogger(__name__)


class EmptyDocument(ValueError):
    pass


@dataclass
class Source:
    pages: list[Page]
    ocr_provider: str | None = None
    filename: str | None = None


def read_source(filename: str | None, data: bytes | None, text: str | None, ocr: OCRProvider | None) -> Source:
    """Turn an upload or pasted text into pages, running OCR on scanned pages when a provider exists."""
    if data is not None and filename:
        ext = PurePath(filename).suffix.lower()
        name = PurePath(filename).name
        if ext in IMAGE_EXT:
            if not ocr:
                raise EmptyDocument("Rasm fayllari uchun OCR sozlanmagan (Tesseract yoki Claude vision kerak)")
            return Source([Page(1, ocr.ocr_image(data))], ocr.name, name)
        pages = parse_document(filename, data)
        blank = [p.number for p in pages if len(p.text.strip()) < MIN_TEXT_CHARS]
        used: str | None = None
        if ext == ".pdf" and blank and ocr:
            limit = get_settings().ocr_max_pages
            try:
                texts = ocr.ocr_pdf(data, blank[:limit])
            except Exception:
                log.exception("OCR failed")
                texts = {}
            if texts:
                used = ocr.name
                pages = [Page(p.number, texts.get(p.number, p.text) if p.number in texts else p.text) for p in pages]
        return Source(pages, used, name)
    if text and text.strip():
        return Source(parse_text(text))
    raise EmptyDocument("Fayl yoki matn kerak")


def create_tender(
    db: Session, company: Company, title: str, buyer: str | None, src: Source,
    llm: extractor.LLMClient | None, started: float | None = None,
) -> Tender:
    started = started or time.perf_counter()
    if not any(p.text.strip() for p in src.pages):
        raise EmptyDocument(
            "Hujjatda matn topilmadi. Skaner qilingan PDF bo'lsa, OCR sozlang (BIDBRIDGE_OCR_PROVIDER) yoki matnli fayl yuboring"
        )
    settings = get_settings()
    reqs = extractor.extract(src.pages, llm, settings.llm_max_chars_per_call)
    t = Tender(
        company_id=company.id, title=title[:500], buyer=buyer, source_filename=src.filename, page_count=len(src.pages),
        deadline=extractor.pick_deadline(reqs), extraction_mode="rules+llm" if llm else "rules",
        ocr_provider=src.ocr_provider, analysis_seconds=round(time.perf_counter() - started, 3),
    )
    t.requirements = [
        Requirement(
            type=r.type.value, label=r.label, doc_kind=r.doc_kind.value if r.doc_kind else None, subject=r.subject,
            op=r.op, value=r.value, unit=r.unit, currency=r.currency, due=r.due, critical=r.critical,
            quote=r.quote, page=r.page, source=r.source,
        )
        for r in reqs
    ]
    db.add(t)
    db.commit()
    return t
