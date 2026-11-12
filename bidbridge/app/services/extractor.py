"""Tender requirement extraction.

Design rule: a requirement is only kept if it carries a quote that can be located
verbatim (after whitespace/case normalisation) on the cited page. This makes the
output auditable and prevents invented requirements, for both the rule engine and
the optional LLM path.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from app.domain import DOC_LABELS_UZ, DocKind, ReqType
from app.services.ingest import Page
from app.services.textutil import find_amounts, find_dates, normalize

log = logging.getLogger(__name__)

_NUM = r"\d{1,3}(?:[  ,]\d{3})+|\d+(?:[.,]\d+)?"
_MIN = r"(?:kamida|kam bo'lmagan|at least|minimum(?: of)?|not less than|no less than|не менее|минимум)"
_MAX = r"(?:ko'pi bilan|oshmasligi|not more than|at most|no more than|maximum(?: of)?|не более)"
_NUMERIC_RE = re.compile(rf"(?P<cue>{_MIN}|{_MAX})\s+(?P<num>{_NUM})\s*(?P<tail>[^.;:,]{{0,60}})")
_UZ_POSTFIX_RE = re.compile(rf"(?P<num>{_NUM})\s*(?P<tail>[a-z']{{0,20}})\s+dan\s+kam\s+(?:bo'lmagan|emas)")
_UZ_MAX_POSTFIX_RE = re.compile(rf"(?P<num>{_NUM})\s*(?P<tail>[a-z']{{0,20}}?)dan\s+(?:oshmasligi|ko'p bo'lmasligi)")
_YEARS_RE = re.compile(r"^\s*(?:yil|yillik|year|years|года|год|лет)\b")
_CONTRACTS_RE = re.compile(
    r"^\s*(?:(?:ta|dona|ea|pcs)\s+)?(?:(?:o'xshash|similar|аналогичн\w*)\s+)?"
    r"(?:shartnoma|contract|loyiha|project|obyekt|ob'yekt|договор|проект|объект)",
)
_TURNOVER_CUE = re.compile(r"aylanma|daromad|turnover|revenue|оборот|выручк")
_SECURITY_CUE = re.compile(
    r"taklif ta'minot|bank kafolat|bid security|tender security|bid bond|garov|обеспечени\w* (?:заявк|тендер)|банковск\w+ гаранти"
)
_DEADLINE_CUE = re.compile(
    r"muddat|oxirgi sana|gacha|deadline|before|no later|not later|until|срок|не позднее|до \d|last date|qabul qilish"
)
_SUBMISSION_CUE = re.compile(r"taklif|ariza|bid|tender|offer|proposal|заявк|предложени|конкурс")
_RISK_CUE = re.compile(
    r"rad etil|qabul qilinmay|ko'rib chiqilmay|will be rejected|shall be rejected|may be rejected|disqualif|отклон|не принима|chetlashtir"
)
_DOC_CUE = re.compile(
    r"taqdim|ilova|kerak|talab|lozim|majburiy|submit|provide|enclos|must|required|attach|"
    r"представ|приложи|необходим|обязатель|ko'rsatilgan|bo'lishi"
)

# Ordered most-specific first. A single unit may yield several kinds.
_DOC_RULES: list[tuple[DocKind, re.Pattern[str]]] = [
    (DocKind.BID_SECURITY, _SECURITY_CUE),
    (DocKind.GUARANTEE_LETTER, re.compile(r"kafolat xati|letter of guarantee|гарантийн\w+ письм|письм\w*-гарант")),
    (DocKind.ANTI_CORRUPTION, re.compile(r"korrupsiya|anti-?corruption|антикоррупц")),
    (DocKind.APPLICATION_FORM, re.compile(r"qatnashish ariza|ishtirok\w* ariza|anketa|questionnaire|заявк\w+ на участие|letter of bid|application for participation|application form")),
    (DocKind.TECHNICAL_PROPOSAL, re.compile(r"texnik taklif|technical proposal|техническ\w+ предложени")),
    (DocKind.PRICE_PROPOSAL, re.compile(r"narx taklif|price proposal|financial proposal|price schedule|ценов\w+ предложени")),
    (DocKind.BANKRUPTCY_STATEMENT, re.compile(r"bankrot|tugatilish|tugatish|reorganizatsiya|liquidation|bankruptcy|ликвидац|банкрот")),
    (DocKind.TAX_CLEARANCE, re.compile(r"soliq\w* qarz|soliq va boshqa majburiy|tax clearance|tax arrears|налогов\w+ задолж|задолженност\w+ по налог|qarzdorlik yo'q")),
    (DocKind.TIN_CERTIFICATE, re.compile(r"\bstir\b|soliq to'lovchi|taxpayer identification|tin certificate|\bинн\b")),
    (DocKind.REGISTRATION_CERTIFICATE, re.compile(r"ro'yxatdan o'tganlik|ro'yxatdan o'tkazilganlik|davlat ro'yxati|certificate of incorporation|registration certificate|свидетельств\w+ о (?:государственной )?регистрации")),
    (DocKind.POWER_OF_ATTORNEY, re.compile(r"ishonchnoma|power of attorney|доверенност|vakolat")),
    (DocKind.LICENSE, re.compile(r"litsenziya|licen[sc]e|лицензи")),
    (DocKind.MANUFACTURER_AUTHORIZATION, re.compile(r"avtorizatsiya|authori[sz]ation letter|manufacturer'?s authori[sz]ation|dilerlik|дилерск|авторизац")),
    (DocKind.FINANCIAL_STATEMENT, re.compile(r"moliyaviy hisobot|balans|financial statement|бухгалтерск\w+ баланс|финансов\w+ отчет")),
    (DocKind.EXPERIENCE_REFERENCE, re.compile(r"tajriba\w*\s+(?:ma'lumotnoma|jadval|tasdiqlovchi|haqida)|experience\s+(?:certificate|reference|table|record|statement)|справк\w+ об опыте")),
]
_GENERIC_CERT = re.compile(r"sertifikat|certificate|сертификат")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s")
_DOC_HEADING = re.compile(r"hujjat|document|документ|ilova|appendix|annex")

_BULLET = re.compile(r"^\s*(?:[-•*▪●○]|\d+[.)]|[a-zA-Z][.)]|\([a-zA-Z0-9]\))\s+")
_SPLIT = re.compile(r"(?<=[.;!?])\s+(?=[A-ZА-ЯЎҚҒҲ0-9•\-])|;\s+")


@dataclass
class ExtractedRequirement:
    type: ReqType
    label: str
    quote: str
    page: int
    doc_kind: DocKind | None = None
    subject: str | None = None
    op: str | None = None
    value: float | None = None
    unit: str | None = None
    currency: str | None = None
    due: date | None = None
    critical: bool = False
    source: str = "rules"
    extra: dict[str, str] = field(default_factory=dict)

    def key(self) -> tuple[object, ...]:
        return (
            self.type,
            self.doc_kind,
            self.op,
            round(self.value, 2) if self.value is not None else None,
            self.due,
            self.currency,
            " ".join(normalize(self.subject or "").split()[-4:]) if self.type == ReqType.NUMERIC else None,
        )


def _units(page_text: str) -> list[str]:
    """Rebuild logical sentences from PDF-style hard-wrapped lines."""
    units: list[str] = []
    cur: list[str] = []
    for raw in page_text.splitlines():
        line = raw.strip()
        if not line:
            if cur:
                units.append(" ".join(cur))
                cur = []
            continue
        if cur and _BULLET.match(raw):
            units.append(" ".join(cur))
            cur = []
        cur.append(line)
    if cur:
        units.append(" ".join(cur))
    out: list[str] = []
    for u in units:
        out.extend(p.strip() for p in _SPLIT.split(u) if p.strip())
    return out


def _to_number(raw: str) -> float:
    if re.fullmatch(r"\d{1,3}(?:[  ,]\d{3})+", raw):
        return float(re.sub(r"[  ,]", "", raw))
    return float(raw.replace(",", "."))


def _quote(unit: str) -> str:
    return unit if len(unit) <= 400 else unit[:397] + "..."


def locate_quote(quote: str, pages: list[Page]) -> int | None:
    """Return the page number on which ``quote`` appears, or None if it does not."""
    q = normalize(quote.rstrip(".").removesuffix("..."))
    if len(q) < 8:
        return None
    for p in pages:
        if q in normalize(p.text):
            return p.number
    return None


def _subject_before(norm: str, start: int, tail: str) -> str:
    before = re.findall(r"[a-zЀ-ӿ']+", norm[:start])[-10:]
    words = " ".join(before)
    return f"{words} {tail}".strip()


def extract_rules(pages: list[Page]) -> list[ExtractedRequirement]:
    """Deterministic, offline extraction for Uzbek / Russian / English tender text."""
    reqs: list[ExtractedRequirement] = []
    seen_docs: set[DocKind] = set()
    risks = 0
    doc_context = False  # True while inside a "documents to submit" section

    for page in pages:
        for unit in _units(page.text):
            norm = normalize(unit)
            if not _BULLET.match(unit) and (_NUMBERED.match(unit) or (len(norm) < 70 and not unit.rstrip().endswith((".", ";", ",")))):
                if _DOC_HEADING.search(norm):
                    doc_context = True
                elif not any(rx.search(norm) for _, rx in _DOC_RULES) and not _GENERIC_CERT.search(norm):
                    doc_context = False  # a new non-document heading ends the document list
            if len(norm) < 12:
                continue
            quote = _quote(unit)

            # --- amounts: turnover / bid security -------------------------------------
            amounts = find_amounts(unit)
            handled_amount = False
            if amounts and _TURNOVER_CUE.search(norm):
                a = amounts[0]
                reqs.append(ExtractedRequirement(
                    ReqType.TURNOVER, "Yillik aylanma talabi", quote, page.number,
                    op=">=", value=a.value, currency=a.currency,
                ))
                handled_amount = True
            elif amounts and _SECURITY_CUE.search(norm):
                a = amounts[0]
                reqs.append(ExtractedRequirement(
                    ReqType.SECURITY, "Taklif ta'minoti summasi", quote, page.number,
                    doc_kind=DocKind.BID_SECURITY, op="==", value=a.value, currency=a.currency,
                ))
                seen_docs.add(DocKind.BID_SECURITY)
                handled_amount = True

            # --- numeric thresholds / experience --------------------------------------
            if not handled_amount:
                for m in list(_NUMERIC_RE.finditer(norm)) + list(_UZ_POSTFIX_RE.finditer(norm)) + list(_UZ_MAX_POSTFIX_RE.finditer(norm)):
                    num = _to_number(m.group("num"))
                    tail = m.group("tail") or ""
                    cue = m.groupdict().get("cue") or ""
                    op = "<=" if (re.fullmatch(_MAX, cue) or m.re is _UZ_MAX_POSTFIX_RE) else ">="
                    if _YEARS_RE.match(tail) and op == ">=":
                        reqs.append(ExtractedRequirement(
                            ReqType.EXPERIENCE, "Faoliyat/tajriba muddati", quote, page.number,
                            subject="years", op=op, value=num, unit="yil"))
                    elif _CONTRACTS_RE.match(tail) and op == ">=":
                        reqs.append(ExtractedRequirement(
                            ReqType.EXPERIENCE, "O'xshash shartnomalar soni", quote, page.number,
                            subject="contracts", op=op, value=num, unit="ta"))
                    else:
                        unit_word = (re.findall(r"[a-zЀ-ӿ'%]+", tail) or [""])[0]
                        subject = _subject_before(norm, m.start(), tail)
                        reqs.append(ExtractedRequirement(
                            ReqType.NUMERIC, "Raqamli talab", quote, page.number,
                            subject=subject, op=op, value=num, unit=unit_word))

            # --- documents ------------------------------------------------------------
            if doc_context or _DOC_CUE.search(norm):
                matches = [kind for kind, rx in _DOC_RULES if rx.search(norm)]
                if DocKind.GUARANTEE_LETTER in matches:
                    # The guarantee letter *is* the deliverable for these statements.
                    matches = [k for k in matches if k not in (DocKind.BANKRUPTCY_STATEMENT, DocKind.TAX_CLEARANCE)]
                if handled_amount:
                    matches = [k for k in matches if k != DocKind.BID_SECURITY]
                for kind in matches:
                    if kind in seen_docs:
                        continue
                    seen_docs.add(kind)
                    reqs.append(ExtractedRequirement(
                        ReqType.DOCUMENT, DOC_LABELS_UZ[kind], quote, page.number, doc_kind=kind))
                if not matches and _GENERIC_CERT.search(norm) and DocKind.CERTIFICATE not in seen_docs:
                    seen_docs.add(DocKind.CERTIFICATE)
                    reqs.append(ExtractedRequirement(
                        ReqType.DOCUMENT, DOC_LABELS_UZ[DocKind.CERTIFICATE], quote, page.number,
                        doc_kind=DocKind.CERTIFICATE))

            # --- deadlines ------------------------------------------------------------
            if _DEADLINE_CUE.search(norm) and _SUBMISSION_CUE.search(norm):
                dates = find_dates(unit)
                if dates:
                    reqs.append(ExtractedRequirement(
                        ReqType.DEADLINE, "Taklif topshirish muddati", quote, page.number, due=dates[0]))

            # --- rejection triggers ---------------------------------------------------
            if risks < 10 and _RISK_CUE.search(norm):
                risks += 1
                reqs.append(ExtractedRequirement(
                    ReqType.RISK, "Rad etish sharti", quote, page.number, critical=True))

    return _dedupe(reqs)


def _dedupe(reqs: list[ExtractedRequirement]) -> list[ExtractedRequirement]:
    seen: set[tuple[object, ...]] = set()
    out: list[ExtractedRequirement] = []
    for r in reqs:
        k = r.key() if r.type != ReqType.RISK else (r.type, normalize(r.quote)[:80])
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def pick_deadline(reqs: list[ExtractedRequirement]) -> date | None:
    dates = [r.due for r in reqs if r.type == ReqType.DEADLINE and r.due]
    return min(dates) if dates else None


# ----------------------------------------------------------------------------------------
# Optional LLM path
# ----------------------------------------------------------------------------------------
class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


_SYSTEM = (
    "You extract bid requirements from tender documents (Uzbek, Russian or English). "
    "Return ONLY JSON: {\"requirements\":[{\"type\":\"document|numeric|experience|turnover|security|deadline|risk\","
    "\"label\":str,\"doc_kind\":str|null,\"subject\":str|null,\"op\":\">=|<=|==\"|null,\"value\":number|null,"
    "\"unit\":str|null,\"currency\":\"UZS|USD|EUR|BHM\"|null,\"due\":\"YYYY-MM-DD\"|null,\"critical\":bool,"
    "\"quote\":str,\"page\":int}]}. "
    "RULES: 'quote' MUST be copied verbatim from the page text (max 300 chars). Never infer or invent a requirement "
    "that has no supporting sentence. doc_kind must be one of: "
    + ", ".join(k.value for k in DocKind)
    + ". Use experience with subject 'years' or 'contracts'. Skip boilerplate."
)


class AnthropicClient:
    """Thin wrapper; imported lazily so the app runs without the SDK or a key."""

    def __init__(self, api_key: str, model: str) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def complete(self, system: str, user: str) -> str:
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _parse_llm_json(raw: str) -> list[dict[str, object]]:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    items = data.get("requirements", []) if isinstance(data, dict) else []
    return [i for i in items if isinstance(i, dict)]


def extract_llm(pages: list[Page], client: LLMClient, max_chars: int = 60_000) -> list[ExtractedRequirement]:
    """LLM extraction with mandatory quote verification; unverifiable items are dropped."""
    out: list[ExtractedRequirement] = []
    batch: list[Page] = []
    size = 0

    def flush() -> None:
        nonlocal batch, size
        if not batch:
            return
        prompt = "\n\n".join(f"=== PAGE {p.number} ===\n{p.text}" for p in batch)
        try:
            raw = client.complete(_SYSTEM, prompt)
        except Exception:  # network/API errors must never break the rules path
            log.exception("LLM extraction failed for pages %s", [p.number for p in batch])
            raw = ""
        for item in _parse_llm_json(raw):
            req = _coerce(item, pages)
            if req:
                out.append(req)
        batch, size = [], 0

    for p in pages:
        if size + len(p.text) > max_chars and batch:
            flush()
        batch.append(p)
        size += len(p.text)
    flush()
    return out


def _coerce(item: dict[str, object], pages: list[Page]) -> ExtractedRequirement | None:
    try:
        rtype = ReqType(str(item.get("type")))
    except ValueError:
        return None
    quote = str(item.get("quote") or "").strip()
    page_no = locate_quote(quote, pages)
    if page_no is None:
        return None  # not verbatim in the source -> discard
    kind: DocKind | None = None
    if item.get("doc_kind"):
        try:
            kind = DocKind(str(item["doc_kind"]))
        except ValueError:
            kind = None
    if rtype == ReqType.DOCUMENT and kind is None:
        return None
    due: date | None = None
    if item.get("due"):
        try:
            due = date.fromisoformat(str(item["due"]))
        except ValueError:
            due = None
    value = item.get("value")
    return ExtractedRequirement(
        type=rtype,
        label=str(item.get("label") or (DOC_LABELS_UZ.get(kind, "") if kind else rtype.value)),
        quote=quote,
        page=page_no,
        doc_kind=kind,
        subject=str(item["subject"]) if item.get("subject") else None,
        op=str(item["op"]) if item.get("op") in {">=", "<=", "=="} else None,
        value=float(value) if isinstance(value, (int, float)) else None,
        unit=str(item["unit"]) if item.get("unit") else None,
        currency=str(item["currency"]) if item.get("currency") else None,
        due=due,
        critical=bool(item.get("critical")),
        source="llm",
    )


def extract(pages: list[Page], llm: LLMClient | None = None, max_chars: int = 60_000) -> list[ExtractedRequirement]:
    reqs = extract_rules(pages)
    if llm is not None:
        reqs = _dedupe(reqs + extract_llm(pages, llm, max_chars))
    return reqs
