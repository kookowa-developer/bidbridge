from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from app import schemas as s
from app.config import Settings, get_settings
from app.db import get_session
from app.deps import child_for, company_for
from app.domain import DocKind
from app.models import (
    ROLES, Company, CompanyAttribute, CompanyDocument, CompanyMember, Contract, FinancialYear, User,
)
from app.security import current_user
from app.services.deadlines import build_alerts

router = APIRouter(prefix="/api", tags=["passport"])
_ALLOWED_DOC_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".docx", ".txt"}


@router.get("/companies", response_model=list[s.CompanyOut])
def list_companies(db: Session = Depends(get_session), user: User = Depends(current_user)) -> list[Company]:
    return list(db.scalars(select(Company).join(CompanyMember).where(CompanyMember.user_id == user.id).order_by(Company.id)))


@router.post("/companies", response_model=s.CompanyOut, status_code=201)
def create_company(body: s.CompanyIn, db: Session = Depends(get_session), user: User = Depends(current_user)) -> Company:
    c = Company(**body.model_dump())
    c.members = [CompanyMember(user_id=user.id, role="owner")]
    db.add(c)
    db.commit()
    return c


@router.put("/companies/{company_id}", response_model=s.CompanyOut)
def update_company(company_id: int, body: s.CompanyIn, db: Session = Depends(get_session),
                   user: User = Depends(current_user)) -> Company:
    c = company_for(db, user, company_id, write=True)
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    db.commit()
    return c


@router.get("/companies/{company_id}", response_model=s.PassportOut)
def get_passport(company_id: int, db: Session = Depends(get_session), user: User = Depends(current_user)) -> s.PassportOut:
    c = company_for(db, user, company_id)
    return s.PassportOut(
        company=s.CompanyOut.model_validate(c),
        documents=[s.DocumentOut.model_validate(d) for d in c.documents],
        attributes=[s.AttributeOut.model_validate(a) for a in c.attributes],
        contracts=[s.ContractOut.model_validate(x) for x in c.contracts],
        financials=[s.FinancialOut.model_validate(f) for f in sorted(c.financials, key=lambda f: f.year)],
        doc_kinds=s.doc_kind_choices(),
    )


# ---------------------------------------------------------------- team
@router.get("/companies/{company_id}/members", response_model=list[s.MemberOut])
def list_members(company_id: int, db: Session = Depends(get_session), user: User = Depends(current_user)) -> list[s.MemberOut]:
    c = company_for(db, user, company_id)
    return [s.MemberOut(user_id=m.user_id, email=m.user.email, full_name=m.user.full_name, role=m.role) for m in c.members]


@router.post("/companies/{company_id}/members", response_model=s.MemberOut, status_code=201)
def add_member(company_id: int, body: s.MemberIn, db: Session = Depends(get_session),
               user: User = Depends(current_user)) -> s.MemberOut:
    c = company_for(db, user, company_id, owner=True)
    if body.role not in ROLES or body.role == "owner":
        raise HTTPException(422, f"Rol quyidagilardan biri bo'lishi kerak: {', '.join(r for r in ROLES if r != 'owner')}")
    target = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not target:
        raise HTTPException(404, "Bu email bilan foydalanuvchi topilmadi (avval ro'yxatdan o'tishi kerak)")
    existing = next((m for m in c.members if m.user_id == target.id), None)
    if existing and existing.role == "owner":
        raise HTTPException(409, "Egasining rolini o'zgartirib bo'lmaydi")
    if existing:
        existing.role = body.role
    else:
        existing = CompanyMember(company_id=c.id, user_id=target.id, role=body.role)
        db.add(existing)
    db.commit()
    return s.MemberOut(user_id=target.id, email=target.email, full_name=target.full_name, role=body.role)


# ---------------------------------------------------------------- documents
@router.post("/companies/{company_id}/documents", response_model=s.DocumentOut, status_code=201)
async def add_document(
    company_id: int,
    kind: str = Form(...),
    title: str = Form(..., max_length=300),
    issued_date: date | None = Form(None),
    expires_date: date | None = Form(None),
    fields: str = Form("{}"),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_session),
    user: User = Depends(current_user),
    settings: Settings = Depends(get_settings),
) -> CompanyDocument:
    c = company_for(db, user, company_id, write=True)
    try:
        DocKind(kind)
    except ValueError:
        raise HTTPException(422, f"Noma'lum hujjat turi: {kind}") from None
    try:
        parsed = json.loads(fields or "{}")
        if not isinstance(parsed, dict):
            raise ValueError
        doc_fields = {str(k)[:50]: str(v)[:300] for k, v in parsed.items()}
    except ValueError:
        raise HTTPException(422, "fields to'g'ri JSON obyekt bo'lishi kerak") from None

    filename = content = ctype = None
    if file and file.filename:
        from pathlib import PurePath
        filename = PurePath(file.filename).name[:300]
        if PurePath(filename).suffix.lower() not in _ALLOWED_DOC_EXT:
            raise HTTPException(415, "Ruxsat etilgan formatlar: " + ", ".join(sorted(_ALLOWED_DOC_EXT)))
        content = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
        if len(content) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"Fayl juda katta (chegara {settings.max_upload_mb} MB)")
        ctype = "application/octet-stream"  # never echo a client-supplied type back to browsers
    d = CompanyDocument(company_id=c.id, kind=kind, title=title, issued_date=issued_date, expires_date=expires_date,
                        fields=doc_fields, filename=filename, content=content, content_type=ctype)
    db.add(d)
    db.commit()
    return d


@router.get("/documents/{doc_id}/download")
def download_document(doc_id: int, db: Session = Depends(get_session), user: User = Depends(current_user)) -> Response:
    d = db.scalar(select(CompanyDocument).options(undefer(CompanyDocument.content)).where(CompanyDocument.id == doc_id))
    if not d:
        raise HTTPException(404, "Topilmadi")
    company_for(db, user, d.company_id)
    if not d.content:
        raise HTTPException(404, "Faylsiz hujjat")
    safe = (d.filename or f"document_{d.id}").replace('"', "")
    return Response(d.content, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{safe}"', "X-Content-Type-Options": "nosniff"})


def _delete(db: Session, user: User, model: type, obj_id: int) -> Response:
    obj = child_for(db, user, model, obj_id)
    db.delete(obj)
    db.commit()
    return Response(status_code=204)


@router.delete("/documents/{doc_id}", status_code=204)
def delete_document(doc_id: int, db: Session = Depends(get_session), user: User = Depends(current_user)) -> Response:
    return _delete(db, user, CompanyDocument, doc_id)


@router.post("/companies/{company_id}/attributes", response_model=s.AttributeOut, status_code=201)
def add_attribute(company_id: int, body: s.AttributeIn, db: Session = Depends(get_session),
                  user: User = Depends(current_user)) -> CompanyAttribute:
    company_for(db, user, company_id, write=True)
    a = CompanyAttribute(company_id=company_id, **body.model_dump())
    db.add(a)
    db.commit()
    return a


@router.delete("/attributes/{attr_id}", status_code=204)
def delete_attribute(attr_id: int, db: Session = Depends(get_session), user: User = Depends(current_user)) -> Response:
    return _delete(db, user, CompanyAttribute, attr_id)


@router.post("/companies/{company_id}/contracts", response_model=s.ContractOut, status_code=201)
def add_contract(company_id: int, body: s.ContractIn, db: Session = Depends(get_session),
                 user: User = Depends(current_user)) -> Contract:
    company_for(db, user, company_id, write=True)
    x = Contract(company_id=company_id, **body.model_dump())
    db.add(x)
    db.commit()
    return x


@router.delete("/contracts/{contract_id}", status_code=204)
def delete_contract(contract_id: int, db: Session = Depends(get_session), user: User = Depends(current_user)) -> Response:
    return _delete(db, user, Contract, contract_id)


@router.put("/companies/{company_id}/financials", response_model=s.FinancialOut)
def upsert_financial(company_id: int, body: s.FinancialIn, db: Session = Depends(get_session),
                     user: User = Depends(current_user)) -> FinancialYear:
    c = company_for(db, user, company_id, write=True)
    row = next((f for f in c.financials if f.year == body.year), None)
    if row:
        row.revenue_uzs = body.revenue_uzs
    else:
        row = FinancialYear(company_id=c.id, **body.model_dump())
        db.add(row)
    db.commit()
    return row


@router.get("/companies/{company_id}/alerts", response_model=list[s.AlertOut])
def alerts(company_id: int, today: date | None = None, db: Session = Depends(get_session),
           user: User = Depends(current_user), settings: Settings = Depends(get_settings)) -> list[s.AlertOut]:
    c = company_for(db, user, company_id)
    return [s.AlertOut(severity=a.severity, kind=a.kind, message=a.message, due=a.due, ref_id=a.ref_id)
            for a in build_alerts(c, today or date.today(), settings.expiry_warn_days)]
