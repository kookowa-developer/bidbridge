"""Glue between ORM objects and the pure compliance / bid engines."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.config import Settings
from app.models import Company, Tender
from app.services import bid, compliance as comp


def snapshot(c: Company) -> comp.CompanySnap:
    return comp.CompanySnap(
        legal_name=c.legal_name,
        stir=c.stir,
        registration_date=c.registration_date,
        documents=[comp.DocSnap(d.kind, d.title, d.issued_date, d.expires_date, dict(d.fields or {})) for d in c.documents],
        attributes=[comp.AttrSnap(a.label, a.value, a.unit, a.source) for a in c.attributes],
        contracts=[comp.ContractSnap(x.customer, x.subject, x.amount_uzs, x.completed_date, x.similar) for x in c.contracts],
        revenue_by_year={f.year: f.revenue_uzs for f in c.financials},
    )


@dataclass
class TenderReport:
    results: list[comp.CheckResult]
    consistency: list[comp.CheckResult]
    readiness: comp.Readiness
    snap: comp.CompanySnap


def build_report(t: Tender, today: date, settings: Settings) -> TenderReport:
    snap = snapshot(t.company)
    results = comp.evaluate(snap, list(t.requirements), today, t.deadline, settings.risky_expiry_margin_days)
    cons = comp.check_consistency(snap)
    return TenderReport(results, cons, comp.readiness(results + cons), snap)


def bid_decision(t: Tender, rep: TenderReport, today: date) -> bid.BidDecision:
    security = next((r.value for r in t.requirements
                     if r.type == "security" and (r.currency or "UZS") == "UZS" and r.value), None)
    return bid.decide(rep.results, rep.consistency, rep.readiness, rep.snap, t.deadline, today, security)
