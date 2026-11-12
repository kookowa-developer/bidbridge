from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.domain import DocKind


class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CompanyIn(BaseModel):
    legal_name: str = Field(min_length=2, max_length=300)
    stir: str | None = None
    registration_date: date | None = None
    address: str | None = None
    director: str | None = None
    phone: str | None = None
    email: str | None = None
    bank_details: str | None = None


class DocumentOut(_ORM):
    id: int
    kind: str
    title: str
    issued_date: date | None
    expires_date: date | None
    fields: dict[str, str]
    filename: str | None


class AttributeIn(BaseModel):
    label: str = Field(min_length=2, max_length=300)
    value: float
    unit: str | None = None
    source: str | None = None


class AttributeOut(AttributeIn, _ORM):
    id: int


class ContractIn(BaseModel):
    customer: str
    subject: str
    amount_uzs: float | None = None
    completed_date: date | None = None
    similar: bool = True


class ContractOut(ContractIn, _ORM):
    id: int


class FinancialIn(BaseModel):
    year: int = Field(ge=1990, le=2100)
    revenue_uzs: float = Field(ge=0)


class FinancialOut(FinancialIn, _ORM):
    id: int


class CompanyOut(CompanyIn, _ORM):
    id: int


class PassportOut(BaseModel):
    company: CompanyOut
    documents: list[DocumentOut]
    attributes: list[AttributeOut]
    contracts: list[ContractOut]
    financials: list[FinancialOut]
    doc_kinds: list[dict[str, str]]


class RequirementOut(_ORM):
    id: int
    type: str
    label: str
    doc_kind: str | None
    subject: str | None
    op: str | None
    value: float | None
    unit: str | None
    currency: str | None
    due: date | None
    critical: bool
    quote: str
    page: int
    source: str


class TaskIn(BaseModel):
    title: str
    role: str = "other"
    due: date | None = None


class TaskOut(TaskIn, _ORM):
    id: int
    done: bool


class TaskPatch(BaseModel):
    done: bool | None = None
    due: date | None = None
    role: str | None = None


class TenderOut(_ORM):
    id: int
    company_id: int
    title: str
    buyer: str | None
    deadline: date | None
    page_count: int
    extraction_mode: str
    ocr_provider: str | None
    analysis_seconds: float | None
    requirements: list[RequirementOut]
    tasks: list[TaskOut]


class TenderBrief(_ORM):
    id: int
    title: str
    deadline: date | None


class CheckResultOut(BaseModel):
    requirement_id: int | None
    type: str
    label: str
    status: str
    message: str
    action: str | None
    quote: str | None
    page: int | None
    critical: bool
    generate: str | None


class ReadinessOut(BaseModel):
    score: int
    verdict: str
    ok: int
    warn: int
    fail: int
    manual: int


class CheckReport(BaseModel):
    tender_id: int
    as_of: date
    deadline: date | None
    readiness: ReadinessOut
    results: list[CheckResultOut]
    consistency: list[CheckResultOut]


class AlertOut(BaseModel):
    severity: str
    kind: str
    message: str
    due: date | None
    ref_id: int | None


def doc_kind_choices() -> list[dict[str, str]]:
    from app.domain import DOC_LABELS_UZ, TENDER_SPECIFIC

    return [{"value": k.value, "label": DOC_LABELS_UZ[k]} for k in DocKind if k not in TENDER_SPECIFIC]


class MemberIn(BaseModel):
    email: str
    role: str = "viewer"


class MemberOut(BaseModel):
    user_id: int
    email: str
    full_name: str | None
    role: str


class FactorOut(BaseModel):
    name: str
    weight: float
    score: float
    detail: str


class BidOut(BaseModel):
    recommendation: str
    score: int
    confidence: str
    summary: str
    factors: list[FactorOut]
    blockers: list[str]
    effort_days: float
    days_left: int | None
    assumptions: list[str]


class PilotIn(BaseModel):
    manual_minutes: float = Field(gt=0, le=10_000)
    assisted_minutes: float = Field(gt=0, le=10_000)
    note: str | None = Field(None, max_length=500)


class PilotOut(PilotIn, _ORM):
    id: int
    tender_id: int


class MetricsOut(BaseModel):
    tenders_analysed: int
    avg_analysis_seconds: float | None
    ocr_runs: int
    pilot_samples: int
    mean_reduction_pct: float | None
    median_reduction_pct: float | None
    min_reduction_pct: float | None
    max_reduction_pct: float | None
    target_pct: float
    target_met: bool | None
    note: str


class TelegramLinkOut(BaseModel):
    code: str
    deep_link: str | None
    expires_minutes: int
    bot_configured: bool
