from __future__ import annotations

import io
import json
from datetime import date, timedelta

from docx import Document

from app.domain import DocKind, ReqType, Status
from app.services import compliance as comp
from app.services import docgen
from app.services.extractor import (
    ExtractedRequirement, extract, extract_rules, locate_quote, pick_deadline,
)
from app.services.ingest import Page, parse_document, parse_text
from app.services.textutil import find_amounts, find_dates, overlap

TODAY = date(2026, 11, 12)
DEADLINE = date(2026, 12, 25)


def _reqs(text: str):
    return extract_rules(parse_text(text))


# ------------------------------------------------------------------ text utils
def test_dates_uz_ru_en_numeric():
    t = "2025-yil 30-dekabr, 05.03.2026, 7 марта 2026, June 3, 2026, 2026-01-09"
    assert find_dates(t) == [date(2025, 12, 30), date(2026, 3, 5), date(2026, 3, 7), date(2026, 6, 3), date(2026, 1, 9)]


def test_amounts():
    a = find_amounts("25 000 000 so'm va USD 812,000 hamda 1,5 mlrd so‘m")
    assert [(x.value, x.currency) for x in a] == [(25_000_000, "UZS"), (812_000, "USD"), (1_500_000_000, "UZS")]
    assert find_amounts("500 dona") == []


def test_overlap_handles_uzbek_suffixes():
    assert overlap("ishtirokchining oylik ishlab chiqarish quvvati", "Oylik ishlab chiqarish quvvat") >= 0.8
    assert overlap("xodimlar soni", "oylik ishlab chiqarish quvvati") == 0


# ------------------------------------------------------------------ extractor
def test_extractor_finds_demo_requirements_with_quotes():
    from app.demo import demo_tender_text

    pages = parse_text(demo_tender_text(DEADLINE))
    reqs = extract_rules(pages)
    by_type = lambda t: [r for r in reqs if r.type == t]
    assert pick_deadline(reqs) == DEADLINE
    num = by_type(ReqType.NUMERIC)[0]
    assert (num.op, num.value, num.unit) == (">=", 500.0, "dona")
    assert {(r.subject, r.value) for r in by_type(ReqType.EXPERIENCE)} == {("years", 3.0), ("contracts", 2.0)}
    assert by_type(ReqType.TURNOVER)[0].value == 1_500_000_000
    sec = by_type(ReqType.SECURITY)[0]
    assert (sec.value, sec.currency) == (25_000_000, "UZS")
    kinds = {r.doc_kind for r in by_type(ReqType.DOCUMENT)}
    assert {DocKind.REGISTRATION_CERTIFICATE, DocKind.TIN_CERTIFICATE, DocKind.TAX_CLEARANCE, DocKind.GUARANTEE_LETTER,
            DocKind.EXPERIENCE_REFERENCE, DocKind.CERTIFICATE, DocKind.POWER_OF_ATTORNEY,
            DocKind.TECHNICAL_PROPOSAL, DocKind.PRICE_PROPOSAL} <= kinds
    assert DocKind.BID_SECURITY not in kinds  # covered by the SECURITY requirement
    assert DocKind.BANKRUPTCY_STATEMENT not in kinds  # guarantee letter is the deliverable
    for r in reqs:  # every requirement is auditable
        assert locate_quote(r.quote, pages) == r.page


def test_russian_and_english_requirements():
    reqs = _reqs(
        "Participants must have at least 5 years of experience.\n"
        "Bidders must submit a valid license and tax clearance certificate.\n"
        "Bids must be delivered on or before 15 March 2027. Late bids will be rejected.\n"
        "Участник должен иметь не менее 3 лет опыта.\n"
    )
    assert any(r.type == ReqType.EXPERIENCE and r.value == 5 for r in reqs)
    assert any(r.type == ReqType.EXPERIENCE and r.value == 3 for r in reqs)
    assert pick_deadline(reqs) == date(2027, 3, 15)
    kinds = {r.doc_kind for r in reqs if r.type == ReqType.DOCUMENT}
    assert DocKind.LICENSE in kinds and DocKind.TAX_CLEARANCE in kinds
    assert any(r.type == ReqType.RISK for r in reqs)


def test_hard_wrapped_pdf_lines_are_rejoined():
    reqs = _reqs("Ishtirokchining oylik ishlab chiqarish\nquvvati kamida 500 dona\nbo'lishi lozim.")
    assert len(reqs) == 1 and reqs[0].value == 500


class _FakeLLM:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def complete(self, system: str, user: str) -> str:
        return self.payload


def test_llm_items_are_dropped_unless_quote_is_verbatim():
    quote = "Ishtirokchida Euro-5 emissiya standarti tasdig'i bo'lishi kerak."
    pages = [Page(1, quote)]
    assert extract_rules(pages) == []  # rules alone do not see it
    good = {"type": "document", "doc_kind": "certificate", "label": "Euro-5", "quote": quote, "page": 7}
    invented = {"type": "numeric", "value": 99, "op": ">=", "label": "x", "quote": "Kamida 99 ta zavod bo'lishi shart.", "page": 1}
    bad_kind = {"type": "document", "doc_kind": "nonsense", "quote": quote, "page": 1}
    llm = _FakeLLM("```json\n" + json.dumps({"requirements": [good, invented, bad_kind]}) + "\n```")
    reqs = extract(pages, llm)
    assert len(reqs) == 1
    assert (reqs[0].source, reqs[0].doc_kind, reqs[0].page) == ("llm", DocKind.CERTIFICATE, 1)  # page re-derived from text


def test_llm_failure_falls_back_to_rules():
    class Boom:
        def complete(self, system: str, user: str) -> str:
            raise RuntimeError("api down")

    reqs = extract(parse_text("Taklif ta'minoti 10 000 000 so'm bo'lishi kerak."), Boom())
    assert reqs and reqs[0].type == ReqType.SECURITY


# ------------------------------------------------------------------ compliance
def _company(**kw) -> comp.CompanySnap:
    base = dict(legal_name='"Alfa" MChJ', stir="301234567", registration_date=date(2019, 1, 1))
    base.update(kw)
    return comp.CompanySnap(**base)


def _req(**kw) -> ExtractedRequirement:
    base = dict(type=ReqType.NUMERIC, label="x", quote="q", page=1)
    base.update(kw)
    return ExtractedRequirement(**base)


def test_numeric_mismatch_500_vs_450_is_flagged_before_submission():
    c = _company(attributes=[comp.AttrSnap("Oylik ishlab chiqarish quvvati", 450, "dona", "ISO sertifikati")])
    r = _req(subject="ishtirokchining oylik ishlab chiqarish quvvati", op=">=", value=500, unit="dona")
    res = comp.evaluate(c, [r], TODAY)[0]
    assert res.status == Status.FAIL and "450" in res.message and "500" in res.message and "ISO sertifikati" in res.message


def test_numeric_ok_unmatched_and_unit_warning():
    attr = comp.AttrSnap("Oylik ishlab chiqarish quvvati", 600, "dona")
    ok = comp.evaluate(_company(attributes=[attr]), [_req(subject="oylik ishlab chiqarish quvvati", op=">=", value=500, unit="birlik")], TODAY)[0]
    assert ok.status == Status.OK  # birlik == dona
    warn = comp.evaluate(_company(attributes=[attr]), [_req(subject="oylik ishlab chiqarish quvvati", op=">=", value=500, unit="tonna")], TODAY)[0]
    assert warn.status == Status.WARN
    none = comp.evaluate(_company(), [_req(subject="xodimlar soni", op=">=", value=50, unit="kishi")], TODAY)[0]
    assert none.status == Status.MANUAL  # never guesses a match


def test_document_validity_is_judged_at_the_tender_deadline_not_today():
    doc = comp.DocSnap("license", "Lit", expires=DEADLINE - timedelta(days=3))
    r = _req(type=ReqType.DOCUMENT, doc_kind=DocKind.LICENSE)
    assert comp.evaluate(_company(documents=[doc]), [r], TODAY, DEADLINE)[0].status == Status.FAIL
    doc2 = comp.DocSnap("license", "Lit", expires=DEADLINE + timedelta(days=5))
    assert comp.evaluate(_company(documents=[doc2]), [r], TODAY, DEADLINE)[0].status == Status.WARN
    doc3 = comp.DocSnap("license", "Lit", expires=DEADLINE + timedelta(days=200))
    assert comp.evaluate(_company(documents=[doc3]), [r], TODAY, DEADLINE)[0].status == Status.OK
    assert comp.evaluate(_company(), [r], TODAY, DEADLINE)[0].status == Status.FAIL


def test_stale_tax_clearance_warns_and_tender_specific_docs_are_manual():
    doc = comp.DocSnap("tax_clearance", "Soliq", issued=TODAY - timedelta(days=60))
    r = _req(type=ReqType.DOCUMENT, doc_kind=DocKind.TAX_CLEARANCE)
    assert comp.evaluate(_company(documents=[doc]), [r], TODAY)[0].status == Status.WARN
    g = comp.evaluate(_company(), [_req(type=ReqType.DOCUMENT, doc_kind=DocKind.GUARANTEE_LETTER)], TODAY)[0]
    assert g.status == Status.MANUAL and g.generate == "guarantee_letter"


def test_experience_turnover_security_deadline():
    contracts = [comp.ContractSnap("A", "x"), comp.ContractSnap("B", "y"), comp.ContractSnap("C", "z", similar=False)]
    c = _company(contracts=contracts, revenue_by_year={2024: 1_000, 2025: 5_000})
    res = comp.evaluate(c, [
        _req(type=ReqType.EXPERIENCE, subject="years", op=">=", value=3),
        _req(type=ReqType.EXPERIENCE, subject="years", op=">=", value=10),
        _req(type=ReqType.EXPERIENCE, subject="contracts", op=">=", value=2),
        _req(type=ReqType.EXPERIENCE, subject="contracts", op=">=", value=3),
        _req(type=ReqType.TURNOVER, op=">=", value=4_000, currency="UZS"),
        _req(type=ReqType.TURNOVER, op=">=", value=9_000, currency="UZS"),
        _req(type=ReqType.TURNOVER, op=">=", value=100, currency="USD"),
        _req(type=ReqType.SECURITY, value=1, currency="UZS"),
        _req(type=ReqType.DEADLINE, due=TODAY - timedelta(days=1)),
        _req(type=ReqType.DEADLINE, due=TODAY + timedelta(days=2)),
        _req(type=ReqType.DEADLINE, due=TODAY + timedelta(days=20)),
    ], TODAY)
    assert [r.status for r in res] == [
        Status.OK, Status.FAIL, Status.OK, Status.FAIL, Status.OK, Status.FAIL, Status.MANUAL,
        Status.MANUAL, Status.FAIL, Status.WARN, Status.OK,
    ]


def test_consistency_catches_wrong_stir_name_and_conflicting_attributes():
    c = _company(
        documents=[comp.DocSnap("registration_certificate", "Guvohnoma", fields={"stir": "301234568", "legal_name": "Beta MChJ"}),
                   comp.DocSnap("tin_certificate", "STIR", fields={"stir": "301 234 567", "legal_name": "ALFA MCHJ"})],
        attributes=[comp.AttrSnap("Oylik ishlab chiqarish quvvati", 450, "dona", "A"),
                    comp.AttrSnap("Ishlab chiqarish quvvati oylik", 500, "dona", "B")],
    )
    labels = [(r.label, r.status) for r in comp.check_consistency(c)]
    assert ("STIR mos emas", Status.FAIL) in labels
    assert ("Kompaniya nomi mos emas", Status.FAIL) in labels
    assert ("Ko'rsatkichlar ziddiyati", Status.WARN) in labels
    assert comp.check_consistency(_company()) == []


def test_readiness_score_and_verdict():
    mk = lambda st, t="document": comp.CheckResult(None, t, "x", st, "m")
    r = comp.readiness([mk(Status.OK), mk(Status.OK), mk(Status.FAIL), mk(Status.MANUAL), mk(Status.FAIL, "deadline")])
    assert (r.score, r.ok, r.fail, r.manual) == (67, 2, 1, 1) and r.verdict.startswith("TAYYOR EMAS")
    assert comp.readiness([mk(Status.OK)]).verdict == "TAYYOR"


# ------------------------------------------------------------------ ingest + docgen
def test_docx_ingest_roundtrip_and_unsupported_format():
    d = Document()
    d.add_paragraph("Ishtirokchi kamida 3 yil faoliyat yuritgan bo'lishi kerak.")
    buf = io.BytesIO()
    d.save(buf)
    pages = parse_document("tender.docx", buf.getvalue())
    assert "kamida 3 yil" in pages[0].text
    import pytest
    from app.services.ingest import UnsupportedFormat
    with pytest.raises(UnsupportedFormat):
        parse_document("x.exe", b"MZ")


def test_docgen_outputs_valid_docx_with_review_note():
    c = _company(contracts=[comp.ContractSnap("Maktab 12", "Mebel", 1_000_000, date(2025, 9, 1))])
    for kind in docgen.KINDS:
        data = docgen.build_docx(kind, c, "Mebel tenderi", "XTB", "Qarshi", "Karimov", today=TODAY)
        text = "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
        assert "tekshirishi shart" in text and "Alfa" in text
    exp = Document(io.BytesIO(docgen.build_docx("experience", c, "t")))
    assert exp.tables[0].rows[1].cells[1].text == "Maktab 12"
