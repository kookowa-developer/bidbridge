"""Compliance checker: compares extracted tender requirements with the company passport
and checks the passport for internal contradictions.

Pure functions over plain dataclasses so the logic is unit-testable without a database.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from app.domain import (
    DOC_LABELS_UZ,
    STALE_AFTER_DAYS,
    TENDER_SPECIFIC,
    DocKind,
    ReqType,
    Status,
)
from app.services.textutil import normalize, overlap

_UNIT_GROUPS: list[frozenset[str]] = [
    frozenset({"birlik", "dona", "ta", "pcs", "units", "unit", "шт", "штук", "eds"}),
    frozenset({"kishi", "xodim", "nafar", "people", "staff", "чел", "человек"}),
    frozenset({"%", "foiz", "percent", "процент"}),
]
_GENERATABLE: dict[DocKind, str] = {
    DocKind.GUARANTEE_LETTER: "guarantee_letter",
    DocKind.APPLICATION_FORM: "application",
    DocKind.EXPERIENCE_REFERENCE: "experience",
}
_LEGAL_FORMS = re.compile(r"\b(mchj|ooo|llc|ao|aj|xk|ooo|oao|jsc|ltd)\b")


@dataclass
class DocSnap:
    kind: str
    title: str
    issued: date | None = None
    expires: date | None = None
    fields: dict[str, str] = field(default_factory=dict)


@dataclass
class AttrSnap:
    label: str
    value: float
    unit: str | None = None
    source: str | None = None


@dataclass
class ContractSnap:
    customer: str
    subject: str
    amount_uzs: float | None = None
    completed: date | None = None
    similar: bool = True


@dataclass
class CompanySnap:
    legal_name: str
    stir: str | None = None
    registration_date: date | None = None
    documents: list[DocSnap] = field(default_factory=list)
    attributes: list[AttrSnap] = field(default_factory=list)
    contracts: list[ContractSnap] = field(default_factory=list)
    revenue_by_year: dict[int, float] = field(default_factory=dict)


class ReqLike(Protocol):
    type: object
    label: str
    quote: str
    page: int
    doc_kind: object
    subject: str | None
    op: str | None
    value: float | None
    unit: str | None
    currency: str | None
    due: date | None
    critical: bool


@dataclass
class CheckResult:
    requirement_id: int | None
    type: str
    label: str
    status: Status
    message: str
    action: str | None = None
    quote: str | None = None
    page: int | None = None
    critical: bool = False
    generate: str | None = None  # docgen template key, when we can produce the document


@dataclass
class Readiness:
    score: int
    verdict: str
    ok: int
    warn: int
    fail: int
    manual: int


def _fmt(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ") if float(n).is_integer() else f"{n:,.2f}".replace(",", " ")


def _unit_family(unit: str | None) -> frozenset[str] | None:
    if not unit:
        return None
    u = normalize(unit)
    for g in _UNIT_GROUPS:
        if u in g:
            return g
    return frozenset({u})


def _kind_value(kind: object) -> str:
    return getattr(kind, "value", str(kind)) if kind is not None else ""


def _type_value(t: object) -> str:
    return getattr(t, "value", str(t))


# ------------------------------------------------------------------------------------------
# Individual requirement checks
# ------------------------------------------------------------------------------------------
def _check_document(req: ReqLike, c: CompanySnap, ref: date, warn_margin: int) -> tuple[Status, str, str | None, str | None]:
    kind_s = _kind_value(req.doc_kind)
    kind = DocKind(kind_s)
    label = DOC_LABELS_UZ[kind]
    docs = [d for d in c.documents if d.kind == kind_s]

    if kind == DocKind.EXPERIENCE_REFERENCE and not docs:
        n = len([x for x in c.contracts if x.similar])
        if n:
            return Status.OK, f"Passportdagi {n} ta shartnomadan tajriba ma'lumotnomasi tayyorlanadi.", \
                "Ma'lumotnomani generatsiya qiling va imzolatib, muhr bosing.", "experience"
        return Status.FAIL, "Tajriba ma'lumotnomasi uchun shartnomalar passportga kiritilmagan.", \
            "Bajarilgan shartnomalarni 'Shartnomalar' bo'limiga kiriting.", None

    if kind in TENDER_SPECIFIC:
        action = {
            DocKind.BID_SECURITY: "Bank bilan taklif ta'minotini (kafolat yoki garov to'lovi) muddatdan oldin rasmiylashtiring.",
            DocKind.GUARANTEE_LETTER: "Kafolat xatini generatsiya qiling, rahbar imzosi va muhr bilan tasdiqlang.",
            DocKind.APPLICATION_FORM: "Qatnashish arizasini generatsiya qiling va tender shakliga moslab tekshiring.",
        }.get(kind, "Bu hujjat aynan shu tender uchun tayyorlanadi.")
        gen = _GENERATABLE.get(kind)
        return Status.MANUAL, f"{label}: tenderga xos hujjat, passportda saqlanmaydi.", action, gen

    if not docs:
        return Status.FAIL, f"{label} passportga yuklanmagan.", f"'{label}' hujjatini passportga yuklang.", None

    # Prefer the document that stays valid the longest (None == no expiry).
    best = max(docs, key=lambda d: d.expires or date.max)
    if best.expires and best.expires < ref:
        return Status.FAIL, f"{label} muddati {best.expires:%d.%m.%Y} da tugagan (tender sanasi {ref:%d.%m.%Y}).", \
            f"'{label}' hujjatini yangilang.", None
    if best.expires and (best.expires - ref).days <= warn_margin:
        return Status.WARN, f"{label} tender sanasidan keyin {(best.expires - ref).days} kundan so'ng tugaydi ({best.expires:%d.%m.%Y}).", \
            "Hujjatni oldindan yangilab qo'yish tavsiya etiladi.", None
    limit = STALE_AFTER_DAYS.get(kind)
    if limit and best.issued and (ref - best.issued).days > limit:
        return Status.WARN, f"{label} {(ref - best.issued).days} kun oldin olingan; xaridor yangi ma'lumotnoma talab qilishi mumkin.", \
            "Tender hujjatidagi yangilik muddatini tekshiring.", None
    return Status.OK, f"{label} mavjud ({best.title}).", None, None


def _check_numeric(req: ReqLike, c: CompanySnap) -> tuple[Status, str, str | None]:
    subject = req.subject or req.label
    scored = [(overlap(subject, a.label), a) for a in c.attributes]
    scored = [(s, a) for s, a in scored if s >= 0.5]
    if not scored or req.value is None:
        return Status.MANUAL, f"Talab: {req.op or '>='} {_fmt(req.value) if req.value is not None else '?'} {req.unit or ''}. " \
            "Passportda mos ko'rsatkich topilmadi.", "Mos ko'rsatkichni passportga qo'shing yoki qo'lda tekshiring."
    _, attr = max(scored, key=lambda x: x[0])
    fam_r, fam_a = _unit_family(req.unit), _unit_family(attr.unit)
    unit_note = ""
    if fam_r and fam_a and fam_r != fam_a:
        unit_note = f" (birliklar farq qiladi: talab '{req.unit}', passport '{attr.unit}')"
    ok = attr.value >= req.value if (req.op or ">=") == ">=" else attr.value <= req.value
    sym = req.op or ">="
    src = f" Manba: {attr.source}." if attr.source else ""
    if ok:
        st = Status.WARN if unit_note else Status.OK
        return st, f"Talab {sym} {_fmt(req.value)}; sizda '{attr.label}' = {_fmt(attr.value)}.{unit_note}{src}", None
    gap = abs(req.value - attr.value)
    return Status.FAIL, f"Nomuvofiqlik: talab {sym} {_fmt(req.value)}, sizda '{attr.label}' = {_fmt(attr.value)} (farq {_fmt(gap)}).{unit_note}{src}", \
        "Ko'rsatkichni tasdiqlovchi hujjatni tekshiring yoki bu tenderda qatnashmaslikni ko'rib chiqing."


def _check_experience(req: ReqLike, c: CompanySnap, ref: date) -> tuple[Status, str, str | None]:
    need = req.value or 0
    if req.subject == "years":
        if not c.registration_date:
            return Status.MANUAL, "Ro'yxatdan o'tgan sana kiritilmagan.", "Kompaniya profilida sanani to'ldiring."
        years = (ref - c.registration_date).days / 365.25
        if years >= need:
            return Status.OK, f"Talab: kamida {_fmt(need)} yil; faoliyat muddati {years:.1f} yil.", None
        return Status.FAIL, f"Talab: kamida {_fmt(need)} yil; faoliyat muddati {years:.1f} yil.", \
            "Guruhdagi aloqador kompaniya tajribasi yoki sheriklik shartlarini tekshiring."
    n = len([x for x in c.contracts if x.similar])
    if n >= need:
        return Status.OK, f"Talab: kamida {_fmt(need)} ta o'xshash shartnoma; passportda {n} ta.", None
    return Status.FAIL, f"Talab: kamida {_fmt(need)} ta o'xshash shartnoma; passportda {n} ta.", \
        "Bajarilgan shartnomalarni passportga to'liq kiriting."


def _check_turnover(req: ReqLike, c: CompanySnap) -> tuple[Status, str, str | None]:
    cur = (req.currency or "UZS").upper()
    if cur not in {"UZS"}:
        return Status.MANUAL, f"Talab: {_fmt(req.value or 0)} {cur}. Valyuta/BHM kursi MVPda avtomatik hisoblanmaydi.", \
            "Aylanmani tender kursi bo'yicha qo'lda solishtiring."
    if not c.revenue_by_year:
        return Status.MANUAL, "Moliyaviy ko'rsatkichlar kiritilmagan.", "Yillik aylanmani passportga kiriting."
    need = req.value or 0
    latest = max(c.revenue_by_year)
    last = c.revenue_by_year[latest]
    if last >= need:
        return Status.OK, f"Talab {_fmt(need)} so'm; {latest}-yil aylanmasi {_fmt(last)} so'm.", None
    other = [y for y, v in c.revenue_by_year.items() if v >= need]
    if other:
        return Status.WARN, f"{latest}-yil aylanmasi {_fmt(last)} < {_fmt(need)}; {', '.join(map(str, sorted(other)))}-yillarda yetarli. Talab qaysi davrni nazarda tutishini tekshiring.", \
            "Tender hujjatidagi davrni aniqlang."
    return Status.FAIL, f"Talab {_fmt(need)} so'm; eng so'nggi ({latest}) aylanma {_fmt(last)} so'm.", \
        "Moliyaviy talabga javob bermaysiz; hamkor yoki konsorsium variantini ko'rib chiqing."


def _check_deadline(req: ReqLike, today: date) -> tuple[Status, str, str | None]:
    if not req.due:
        return Status.MANUAL, "Sana aniqlanmadi.", None
    days = (req.due - today).days
    if days < 0:
        return Status.FAIL, f"Muddat {req.due:%d.%m.%Y} o'tib ketgan.", None
    if days <= 3:
        return Status.WARN, f"Muddatga {days} kun qoldi ({req.due:%d.%m.%Y}).", "Zudlik bilan topshirishga tayyorlaning."
    return Status.OK, f"Muddatga {days} kun qoldi ({req.due:%d.%m.%Y}).", None


def evaluate(
    company: CompanySnap,
    reqs: list[ReqLike],
    today: date,
    deadline: date | None = None,
    warn_margin: int = 14,
    ids: list[int | None] | None = None,
) -> list[CheckResult]:
    ref = deadline or today
    results: list[CheckResult] = []
    for i, r in enumerate(reqs):
        rid = ids[i] if ids else getattr(r, "id", None)
        t = _type_value(r.type)
        generate: str | None = None
        action: str | None = None
        if t == ReqType.DOCUMENT.value:
            status, msg, action, generate = _check_document(r, company, ref, warn_margin)
        elif t == ReqType.NUMERIC.value:
            status, msg, action = _check_numeric(r, company)
        elif t == ReqType.EXPERIENCE.value:
            status, msg, action = _check_experience(r, company, ref)
        elif t == ReqType.TURNOVER.value:
            status, msg, action = _check_turnover(r, company)
        elif t == ReqType.SECURITY.value:
            cur = r.currency or ""
            status = Status.MANUAL
            msg = f"Taklif ta'minoti: {_fmt(r.value or 0)} {cur}."
            action = "Bank kafolati/garov to'lovini muddatdan oldin rasmiylashtiring."
        elif t == ReqType.DEADLINE.value:
            status, msg, action = _check_deadline(r, today)
        else:  # RISK
            status, msg, action = Status.MANUAL, "Rad etish sharti: topshirishdan oldin qo'lda tekshiring.", None
        results.append(CheckResult(
            requirement_id=rid, type=t, label=r.label, status=status, message=msg, action=action,
            quote=r.quote, page=r.page, critical=bool(r.critical), generate=generate,
        ))
    return results


# ------------------------------------------------------------------------------------------
# Cross-document consistency
# ------------------------------------------------------------------------------------------
def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _name_key(s: str) -> str:
    n = normalize(s)
    n = _LEGAL_FORMS.sub("", n)
    return re.sub(r"[^a-zЀ-ӿ0-9]", "", n)


def check_consistency(c: CompanySnap) -> list[CheckResult]:
    out: list[CheckResult] = []

    def fail(label: str, msg: str, action: str) -> None:
        out.append(CheckResult(None, "consistency", label, Status.FAIL, msg, action))

    stirs = {d.title: _digits(d.fields["stir"]) for d in c.documents if d.fields.get("stir")}
    master = _digits(c.stir) if c.stir else None
    if master:
        for title, val in stirs.items():
            if val != master:
                fail("STIR mos emas", f"'{title}' hujjatida STIR {val}, kompaniya profilida {master}.",
                     "Eskirgan yoki xato hujjatni almashtiring.")
    elif len(set(stirs.values())) > 1:
        fail("STIR mos emas", "Hujjatlarda turli STIR raqamlari bor: " + ", ".join(f"{t}={v}" for t, v in stirs.items()) + ".",
             "Qaysi STIR to'g'riligini aniqlang.")

    if c.legal_name:
        key = _name_key(c.legal_name)
        for d in c.documents:
            n = d.fields.get("legal_name")
            if n and _name_key(n) != key:
                fail("Kompaniya nomi mos emas", f"'{d.title}' hujjatidagi nom '{n}', profilda '{c.legal_name}'.",
                     "Nom o'zgargan bo'lsa, tegishli hujjatlarni yangilang.")

    # Same measurable fact stated with different values.
    attrs = c.attributes
    for i, a in enumerate(attrs):
        for b in attrs[i + 1 :]:
            if overlap(a.label, b.label) >= 0.8 and a.value != b.value and _unit_family(a.unit) == _unit_family(b.unit):
                out.append(CheckResult(
                    None, "consistency", "Ko'rsatkichlar ziddiyati", Status.WARN,
                    f"'{a.label}' uchun ikki xil qiymat: {_fmt(a.value)} ({a.source or 'manba yo`q'}) va {_fmt(b.value)} ({b.source or 'manba yo`q'}).",
                    "Qaysi qiymat haqiqiyligini aniqlab, bittasini qoldiring.",
                ))
    return out


def readiness(results: list[CheckResult]) -> Readiness:
    cnt = {s: 0 for s in Status}
    for r in results:
        if r.type == "deadline":
            continue
        cnt[r.status] += 1
    checkable = cnt[Status.OK] + cnt[Status.WARN] + cnt[Status.FAIL]
    score = round(100 * cnt[Status.OK] / checkable) if checkable else 0
    if cnt[Status.FAIL]:
        verdict = "TAYYOR EMAS: to'ldirilishi shart bo'lgan nomuvofiqliklar bor"
    elif cnt[Status.WARN] or cnt[Status.MANUAL]:
        verdict = "DEYARLI TAYYOR: ogohlantirish va qo'lda tekshiruvlar qoldi"
    else:
        verdict = "TAYYOR"
    return Readiness(score, verdict, cnt[Status.OK], cnt[Status.WARN], cnt[Status.FAIL], cnt[Status.MANUAL])
