"""DOCX generators for tender documents that are prepared per tender.

Every output carries a visible reminder that a human must review it before signing.
"""
from __future__ import annotations

import io
from datetime import date

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from app.services.compliance import CompanySnap

KINDS = ("guarantee_letter", "application", "experience")
REVIEW_NOTE = "Diqqat: ushbu hujjat avtomatik tayyorlandi. Imzolashdan oldin yurist/mas'ul xodim tekshirishi shart."


def _base(title: str) -> Document:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(12)
    h = doc.add_paragraph()
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = h.add_run(title)
    run.bold = True
    run.font.size = Pt(14)
    return doc


def _requisites(doc: Document, c: CompanySnap, address: str | None, director: str | None) -> None:
    doc.add_paragraph(f"Ishtirokchi: {c.legal_name}")
    if c.stir:
        doc.add_paragraph(f"STIR: {c.stir}")
    if address:
        doc.add_paragraph(f"Manzil: {address}")
    if director:
        doc.add_paragraph(f"Rahbar: {director}")


def _signature(doc: Document, director: str | None) -> None:
    doc.add_paragraph()
    doc.add_paragraph(f"Rahbar: ______________________ {director or ''}")
    doc.add_paragraph("M.O'.")
    note = doc.add_paragraph()
    r = note.add_run(REVIEW_NOTE)
    r.italic = True
    r.font.size = Pt(9)


def build_docx(
    kind: str,
    company: CompanySnap,
    tender_title: str,
    buyer: str | None = None,
    address: str | None = None,
    director: str | None = None,
    today: date | None = None,
) -> bytes:
    if kind not in KINDS:
        raise ValueError(f"Noma'lum shablon: {kind}")
    today = today or date.today()
    buyer_txt = buyer or "Buyurtmachi"

    if kind == "guarantee_letter":
        doc = _base("KAFOLAT XATI")
        doc.add_paragraph(f"{buyer_txt}ga")
        doc.add_paragraph(f"Sana: {today:%d.%m.%Y}")
        doc.add_paragraph(f"Tanlov (tender) predmeti: {tender_title}")
        _requisites(doc, company, address, director)
        doc.add_paragraph("Ushbu xat bilan tasdiqlaymizki:")
        for line in (
            "kompaniya tugatish (likvidatsiya) yoki qayta tashkil etish jarayonida emas;",
            "kompaniyaga nisbatan bankrotlik jarayoni qo'zg'atilmagan;",
            f"kompaniya {buyer_txt} bilan sud yoki xo'jalik nizosida emas;",
            "kompaniya boshqa ishtirokchilar bilan narx yoki tender natijalariga ta'sir qiluvchi kelishuvga kirmagan;",
            "taqdim etilgan barcha ma'lumotlar to'g'ri va haqqoniy.",
        ):
            doc.add_paragraph(line, style="List Bullet")
        doc.add_paragraph("Shuningdek, O'zbekiston Respublikasining \"Davlat xaridlari to'g'risida\"gi Qonuni talablariga rioya etishni kafolatlaymiz.")
        _signature(doc, director)

    elif kind == "application":
        doc = _base("TENDERDA QATNASHISH UCHUN ARIZA")
        doc.add_paragraph(f"{buyer_txt}ga")
        doc.add_paragraph(f"Sana: {today:%d.%m.%Y}")
        _requisites(doc, company, address, director)
        doc.add_paragraph(
            f"Biz \"{tender_title}\" bo'yicha o'tkazilayotgan tenderda qatnashish niyatimizni bildiramiz. "
            "Tender hujjatlarining shartlari bilan tanishib chiqdik va ularga rozimiz."
        )
        doc.add_paragraph("Ilova qilinadigan hujjatlar ro'yxati tender hujjatiga muvofiq alohida shaklda taqdim etiladi.")
        _signature(doc, director)

    else:  # experience
        doc = _base("TAJRIBA MA'LUMOTNOMASI")
        doc.add_paragraph(f"Sana: {today:%d.%m.%Y}")
        _requisites(doc, company, address, director)
        doc.add_paragraph("Bajarilgan o'xshash shartnomalar:")
        rows = [c for c in company.contracts if c.similar]
        table = doc.add_table(rows=1, cols=5)
        table.style = "Table Grid"
        for cell, text in zip(table.rows[0].cells, ("№", "Buyurtmachi", "Shartnoma predmeti", "Summa (so'm)", "Yakunlangan sana")):
            cell.text = text
        for i, c in enumerate(rows, 1):
            cells = table.add_row().cells
            cells[0].text = str(i)
            cells[1].text = c.customer
            cells[2].text = c.subject
            cells[3].text = f"{c.amount_uzs:,.0f}".replace(",", " ") if c.amount_uzs else ""
            cells[4].text = f"{c.completed:%d.%m.%Y}" if c.completed else ""
        _signature(doc, director)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
