"""Bid / No-Bid decision support.

A transparent weighted model, not a prediction: every factor, weight and assumption is returned so
a human can overrule it. Effort estimates are rough planning constants (override via ``Assumptions``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.domain import ReqType, Status
from app.services.compliance import CheckResult, CompanySnap, Readiness


@dataclass(frozen=True)
class Assumptions:
    days_missing_document: float = 3.0
    days_bid_security: float = 5.0
    days_generatable: float = 1.0
    days_warning: float = 1.0
    days_manual_review: float = 0.5
    buffer: float = 1.5  # safety margin on the effort estimate
    security_ok_ratio: float = 0.01   # bid security <= 1% of annual revenue: comfortable
    security_bad_ratio: float = 0.10  # >= 10%: heavy exposure


@dataclass
class Factor:
    name: str
    weight: float
    score: float  # 0..100
    detail: str


@dataclass
class BidDecision:
    recommendation: str  # GO | CONDITIONAL | NO-GO
    score: int
    confidence: str  # high | medium | low
    summary: str
    factors: list[Factor]
    blockers: list[str]
    effort_days: float
    days_left: int | None
    assumptions: list[str] = field(default_factory=list)


_HARD_TYPES = {ReqType.NUMERIC.value, ReqType.EXPERIENCE.value, ReqType.TURNOVER.value}


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, x))


def decide(
    results: list[CheckResult],
    consistency: list[CheckResult],
    readiness: Readiness,
    company: CompanySnap,
    deadline: date | None,
    today: date,
    security_amount_uzs: float | None = None,
    a: Assumptions = Assumptions(),
) -> BidDecision:
    days_left = (deadline - today).days if deadline else None
    allr = [*results, *consistency]

    # --- blockers: capability gaps that paperwork cannot fix before the deadline
    blockers = [f"{r.label}: {r.message}" for r in results if r.status == Status.FAIL and r.type in _HARD_TYPES]
    if days_left is not None and days_left < 0:
        blockers.append("Topshirish muddati o'tib ketgan.")

    # --- effort
    effort = 0.0
    for r in allr:
        if r.type in {"deadline", "risk"}:
            continue
        if r.status == Status.FAIL:
            effort += a.days_missing_document if r.type == "document" else a.days_warning
        elif r.status == Status.WARN:
            effort += a.days_warning
        elif r.status == Status.MANUAL:
            if r.type == "security":
                effort += a.days_bid_security
            elif r.generate:
                effort += a.days_generatable
            else:
                effort += a.days_manual_review

    factors: list[Factor] = []
    factors.append(Factor("Talablarga moslik", 0.40, _clamp(readiness.score),
                          f"{readiness.ok} ta mos, {readiness.fail} ta nomuvofiq, {readiness.warn} ta ogohlantirish."))

    if days_left is None:
        factors.append(Factor("Vaqt zaxirasi", 0.25, 50, "Topshirish muddati aniqlanmadi."))
    elif effort == 0:
        factors.append(Factor("Vaqt zaxirasi", 0.25, 100, f"{days_left} kun qoldi, qo'shimcha ish talab qilinmaydi."))
    else:
        need = effort * a.buffer
        factors.append(Factor("Vaqt zaxirasi", 0.25, _clamp(100 * days_left / need) if days_left >= 0 else 0,
                              f"{days_left} kun qoldi; taxminiy ish hajmi {effort:.1f} kun (zaxira bilan {need:.1f})."))

    latest = company.revenue_by_year[max(company.revenue_by_year)] if company.revenue_by_year else None
    if security_amount_uzs and latest:
        ratio = security_amount_uzs / latest
        span = a.security_bad_ratio - a.security_ok_ratio
        score = _clamp(100 - 80 * (ratio - a.security_ok_ratio) / span) if ratio > a.security_ok_ratio else 100
        factors.append(Factor("Moliyaviy yuk (taklif ta'minoti)", 0.15, score,
                              f"Ta'minot yillik aylanmaning {ratio:.1%} qismi."))
    else:
        factors.append(Factor("Moliyaviy yuk (taklif ta'minoti)", 0.15, 60, "Ta'minot summasi yoki aylanma ma'lum emas."))

    exp = [r for r in results if r.type == ReqType.EXPERIENCE.value]
    exp_score = 100 * sum(r.status == Status.OK for r in exp) / len(exp) if exp else 70
    factors.append(Factor("Tajriba mosligi", 0.10, exp_score,
                          f"{len(exp)} ta tajriba talabidan {sum(r.status == Status.OK for r in exp)} tasi bajarilgan." if exp
                          else "Tajriba talabi aniqlanmadi."))

    manual_gap = [r for r in results if r.status == Status.MANUAL and r.type in {"numeric", "turnover"} and not r.generate]
    checkable = max(1, len([r for r in results if r.type not in {"deadline", "risk"}]))
    completeness = _clamp(100 * (1 - len(manual_gap) / checkable))
    factors.append(Factor("Ma'lumotlar to'liqligi", 0.10, completeness,
                          f"{len(manual_gap)} ta talab passportda mos ko'rsatkich yo'qligi sababli qo'lda tekshirilishi kerak."))

    score = round(sum(f.weight * f.score for f in factors))
    if blockers:
        rec = "NO-GO"
        summary = "Hujjat bilan to'ldirib bo'lmaydigan to'siqlar bor. Ko'rsatkichni isbotlay olsangiz, qayta baholang."
    elif score >= 75:
        rec, summary = "GO", "Qatnashish tavsiya etiladi."
    elif score >= 50:
        rec, summary = "CONDITIONAL", "Shartli: ko'rsatilgan nomuvofiqliklar va ogohlantirishlarni muddatgacha yopish mumkin bo'lsa."
    else:
        rec, summary = "NO-GO", "Moslik yoki vaqt zaxirasi yetarli emas."
    confidence = "low" if len(manual_gap) / checkable > 0.3 or days_left is None else \
        "medium" if len(manual_gap) else "high"
    return BidDecision(
        recommendation=rec, score=score, confidence=confidence, summary=summary, factors=factors, blockers=blockers,
        effort_days=round(effort, 1), days_left=days_left,
        assumptions=[
            "Ish hajmi taxminiy konstantalarga asoslangan (yetishmayotgan hujjat ~3 kun, bank kafolati ~5 kun) va o'zgartirilishi mumkin.",
            "Model raqobatchilar va narx bo'yicha g'alaba ehtimolini baholamaydi.",
            "Yakuniy qaror rahbariyatga tegishli.",
        ],
    )
