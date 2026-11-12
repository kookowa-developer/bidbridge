from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from pathlib import PurePath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas as s
from app.config import Settings, get_settings
from app.db import get_session
from app.deps import company_for, get_llm, get_ocr_provider, tender_for
from app.models import PilotTiming, Task, Tender, User
from app.security import current_user
from app.services import docgen, extractor
from app.services.ingest import UnsupportedFormat
from app.services.ocr import OCRProvider
from app.services.pipeline import EmptyDocument, create_tender, read_source
from app.services.reports import bid_decision, build_report, snapshot

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["tenders"])


def _out(r) -> s.CheckResultOut:  # noqa: ANN001
    return s.CheckResultOut(
        requirement_id=r.requirement_id, type=r.type, label=r.label, status=r.status.value, message=r.message,
        action=r.action, quote=r.quote, page=r.page, critical=r.critical, generate=r.generate,
    )


@router.post("/companies/{company_id}/tenders", response_model=s.TenderOut, status_code=201)
async def create_tender_endpoint(
    company_id: int,
    title: str = Form(..., max_length=500),
    buyer: str | None = Form(None, max_length=300),
    text: str | None = Form(None),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_session),
    user: User = Depends(current_user),
    llm: extractor.LLMClient | None = Depends(get_llm),
    ocr: OCRProvider | None = Depends(get_ocr_provider),
    settings: Settings = Depends(get_settings),
) -> Tender:
    company = company_for(db, user, company_id, write=True)
    started = time.perf_counter()
    data: bytes | None = None
    filename: str | None = None
    if file and file.filename:
        filename = PurePath(file.filename).name
        data = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
        if len(data) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"Fayl juda katta (chegara {settings.max_upload_mb} MB)")
    try:
        src = read_source(filename, data, text, ocr)
        return create_tender(db, company, title, buyer, src, llm, started)
    except UnsupportedFormat as e:
        raise HTTPException(415, str(e)) from None
    except EmptyDocument as e:
        raise HTTPException(422, str(e)) from None
    except HTTPException:
        raise
    except Exception:
        log.exception("tender ingest failed")
        raise HTTPException(422, "Faylni o'qib bo'lmadi (buzilgan yoki parol bilan himoyalangan bo'lishi mumkin)") from None


@router.get("/companies/{company_id}/tenders", response_model=list[s.TenderBrief])
def list_tenders(company_id: int, db: Session = Depends(get_session), user: User = Depends(current_user)) -> list[Tender]:
    company_for(db, user, company_id)
    return list(db.scalars(select(Tender).where(Tender.company_id == company_id).order_by(Tender.id.desc())))


@router.get("/tenders/{tender_id}", response_model=s.TenderOut)
def get_tender(tender_id: int, db: Session = Depends(get_session), user: User = Depends(current_user)) -> Tender:
    return tender_for(db, user, tender_id)


@router.delete("/tenders/{tender_id}", status_code=204)
def delete_tender(tender_id: int, db: Session = Depends(get_session), user: User = Depends(current_user)) -> Response:
    db.delete(tender_for(db, user, tender_id, write=True))
    db.commit()
    return Response(status_code=204)


@router.get("/tenders/{tender_id}/check", response_model=s.CheckReport)
def check_tender(tender_id: int, today: date | None = None, db: Session = Depends(get_session),
                 user: User = Depends(current_user), settings: Settings = Depends(get_settings)) -> s.CheckReport:
    t = tender_for(db, user, tender_id)
    today = today or date.today()
    rep = build_report(t, today, settings)
    return s.CheckReport(
        tender_id=t.id, as_of=today, deadline=t.deadline, readiness=s.ReadinessOut(**rep.readiness.__dict__),
        results=[_out(x) for x in rep.results], consistency=[_out(x) for x in rep.consistency],
    )


@router.get("/tenders/{tender_id}/bid", response_model=s.BidOut)
def bid(tender_id: int, today: date | None = None, db: Session = Depends(get_session),
        user: User = Depends(current_user), settings: Settings = Depends(get_settings)) -> s.BidOut:
    t = tender_for(db, user, tender_id)
    today = today or date.today()
    d = bid_decision(t, build_report(t, today, settings), today)
    return s.BidOut(
        recommendation=d.recommendation, score=d.score, confidence=d.confidence, summary=d.summary,
        factors=[s.FactorOut(name=f.name, weight=f.weight, score=round(f.score, 1), detail=f.detail) for f in d.factors],
        blockers=d.blockers, effort_days=d.effort_days, days_left=d.days_left, assumptions=d.assumptions,
    )


_ROLE_BY_TYPE = {"turnover": "accountant", "security": "accountant", "numeric": "technical", "experience": "lawyer"}
_ROLE_BY_KIND = {
    "tax_clearance": "accountant", "financial_statement": "accountant", "bid_security": "accountant",
    "license": "lawyer", "certificate": "technical", "technical_proposal": "technical", "price_proposal": "accountant",
}


@router.post("/tenders/{tender_id}/tasks/auto", response_model=list[s.TaskOut])
def auto_tasks(tender_id: int, today: date | None = None, db: Session = Depends(get_session),
               user: User = Depends(current_user), settings: Settings = Depends(get_settings)) -> list[Task]:
    """Turn every failed / manual check that has a concrete action into a role-assigned task."""
    t = tender_for(db, user, tender_id, write=True)
    today = today or date.today()
    rep = build_report(t, today, settings)
    existing = {x.title for x in t.tasks}
    reqs = {r.id: r for r in t.requirements}
    created: list[Task] = []
    for r in rep.results:
        if r.status.value not in {"fail", "manual", "warn"} or not r.action or r.type in {"deadline", "risk"}:
            continue
        title = f"{r.label}: {r.action}"[:300]
        if title in existing:
            continue
        req = reqs.get(r.requirement_id) if r.requirement_id else None
        role = _ROLE_BY_KIND.get(req.doc_kind or "", _ROLE_BY_TYPE.get(r.type, "lawyer")) if req else "lawyer"
        due = max(today, t.deadline - timedelta(days=2)) if t.deadline else None
        task = Task(tender_id=t.id, title=title, role=role, due=due)
        db.add(task)
        created.append(task)
        existing.add(title)
    db.commit()
    return created


@router.post("/tenders/{tender_id}/tasks", response_model=s.TaskOut, status_code=201)
def add_task(tender_id: int, body: s.TaskIn, db: Session = Depends(get_session), user: User = Depends(current_user)) -> Task:
    tender_for(db, user, tender_id, write=True)
    task = Task(tender_id=tender_id, **body.model_dump())
    db.add(task)
    db.commit()
    return task


@router.patch("/tasks/{task_id}", response_model=s.TaskOut)
def patch_task(task_id: int, body: s.TaskPatch, db: Session = Depends(get_session), user: User = Depends(current_user)) -> Task:
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Vazifa topilmadi")
    tender_for(db, user, task.tender_id, write=True)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(task, k, v)
    db.commit()
    return task


@router.get("/tenders/{tender_id}/generate/{kind}")
def generate(tender_id: int, kind: str, db: Session = Depends(get_session), user: User = Depends(current_user)) -> Response:
    t = tender_for(db, user, tender_id)
    if kind not in docgen.KINDS:
        raise HTTPException(404, "Noma'lum shablon")
    c = t.company
    data = docgen.build_docx(kind, snapshot(c), t.title, t.buyer, c.address, c.director)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{kind}_{t.id}.docx"'},
    )


@router.post("/tenders/{tender_id}/pilot", response_model=s.PilotOut, status_code=201)
def add_pilot(tender_id: int, body: s.PilotIn, db: Session = Depends(get_session), user: User = Depends(current_user)) -> PilotTiming:
    """Record how long this tender takes manually vs. with the tool (pilot evidence)."""
    tender_for(db, user, tender_id)
    row = PilotTiming(tender_id=tender_id, user_id=user.id, **body.model_dump())
    db.add(row)
    db.commit()
    return row
