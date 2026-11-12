"""OCR providers for scanned PDFs and images.

Selection (BIDBRIDGE_OCR_PROVIDER): ``auto`` prefers local Tesseract, then Claude vision, else none.
Serverless hosts (Vercel) have no Tesseract binary, so there ``auto`` resolves to Claude vision when
an API key is configured.
"""
from __future__ import annotations

import base64
import io
import logging
import re
import shutil
from typing import Protocol

from app.config import Settings

log = logging.getLogger(__name__)
MIN_TEXT_CHARS = 25  # a PDF page with less text than this is treated as scanned
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
_WANTED_LANGS = ("uzb", "uzb_cyrl", "rus", "eng")


_CYR2LAT = str.maketrans("аеорсухАВЕКМНОРСТХ", "aeopcyxABEKMHOPCTX")
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def fix_homoglyphs(text: str) -> str:
    """Multi-language OCR often emits Cyrillic lookalikes inside Latin words (and vice versa).

    A word is repaired only when it is mostly Latin; a one-letter word follows the majority script of
    its line. Genuinely Cyrillic words are left alone.
    """
    def script(c: str) -> int:
        return 1 if c.isascii() and c.isalpha() else -1 if "\u0400" <= c <= "\u04ff" else 0

    out: list[str] = []
    for line in text.split("\n"):
        line_lat = sum(script(c) == 1 for c in line)
        line_cyr = sum(script(c) == -1 for c in line)

        def repair(m: re.Match[str]) -> str:
            w = m.group(0)
            cyr = sum(script(c) == -1 for c in w)
            lat = sum(script(c) == 1 for c in w)
            if not cyr:
                return w
            mostly_latin = lat > cyr or (len(w) == 1 and line_lat > 2 * line_cyr)
            return w.translate(_CYR2LAT) if mostly_latin else w
        out.append(_WORD_RE.sub(repair, line))
    return "\n".join(out)


class OCRProvider(Protocol):
    name: str

    def ocr_pdf(self, data: bytes, page_numbers: list[int]) -> dict[int, str]: ...

    def ocr_image(self, data: bytes) -> str: ...


class TesseractOCR:
    name = "tesseract"

    def __init__(self, dpi: int = 250) -> None:
        import pytesseract

        self._t = pytesseract
        installed = set(pytesseract.get_languages(config=""))
        langs = [lang for lang in _WANTED_LANGS if lang in installed] or ["eng"]
        self.lang = "+".join(langs)
        self._latin = "+".join(lang for lang in langs if lang not in {"rus", "uzb_cyrl"}) or "eng"
        self._scale = dpi / 72

    def ocr_pdf(self, data: bytes, page_numbers: list[int]) -> dict[int, str]:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(data)
        out: dict[int, str] = {}
        for n in page_numbers:
            if not 1 <= n <= len(pdf):
                continue
            img = pdf[n - 1].render(scale=self._scale).to_pil()
            out[n] = self._read(img)
        return out

    def _read(self, img) -> str:  # type: ignore[no-untyped-def]
        """Mixed Cyrillic+Latin models drop letters in Latin text ("at" -> "а"), so a page that turns
        out to be mostly Latin is re-read with the Latin-only model."""
        text = self._t.image_to_string(img, lang=self.lang)
        if self._latin != self.lang:
            lat = sum(c.isascii() and c.isalpha() for c in text)
            cyr = sum("\u0400" <= c <= "\u04ff" for c in text)
            if lat > 3 * cyr:
                text = self._t.image_to_string(img, lang=self._latin)
        return fix_homoglyphs(text)

    def ocr_image(self, data: bytes) -> str:
        from PIL import Image

        return self._read(Image.open(io.BytesIO(data)))


_PROMPT = (
    "Transcribe the text of this document exactly as written (Uzbek, Russian or English). "
    "Do not translate, summarise or correct. Keep tables as rows separated by ' | '. "
    "{pages}Output each page as a line '=== PAGE n ===' followed by its text."
)


class AnthropicOCR:
    name = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def _ask(self, block: dict[str, object], pages_hint: str) -> str:
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=8000,
            messages=[{"role": "user", "content": [block, {"type": "text", "text": _PROMPT.format(pages=pages_hint)}]}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

    def ocr_pdf(self, data: bytes, page_numbers: list[int]) -> dict[int, str]:
        block = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                  "data": base64.b64encode(data).decode()}}
        hint = f"Only transcribe pages {', '.join(map(str, page_numbers))}. "
        return {n: t for n, t in split_pages(self._ask(block, hint)).items() if n in set(page_numbers)}

    def ocr_image(self, data: bytes) -> str:
        media = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
        block = {"type": "image", "source": {"type": "base64", "media_type": media, "data": base64.b64encode(data).decode()}}
        pages = split_pages(self._ask(block, ""))
        return "\n".join(pages.values()) if pages else ""


def split_pages(text: str) -> dict[int, str]:
    parts = re.split(r"^=== PAGE (\d+) ===\s*$", text, flags=re.MULTILINE)
    return {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def get_ocr(settings: Settings) -> OCRProvider | None:
    choice = settings.ocr_provider.lower()
    if choice == "none":
        return None
    if choice in {"auto", "tesseract"} and shutil.which("tesseract"):
        try:
            return TesseractOCR()
        except Exception:  # pytesseract/pypdfium2 missing
            log.warning("Tesseract binary found but Python bindings unavailable", exc_info=True)
    if choice in {"auto", "anthropic"} and settings.anthropic_api_key:
        return AnthropicOCR(settings.anthropic_api_key, settings.llm_model)
    return None
