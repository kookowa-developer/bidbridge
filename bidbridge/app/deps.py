from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.models import Company, CompanyMember, Tender, User
from app.security import current_user
from app.services import extractor
from app.services.ocr import OCRProvider, get_ocr

WRITE_ROLES = {"owner", "lawyer", "accountant", "technical"}


def get_llm(settings: Settings = Depends(get_settings)) -> extractor.LLMClient | None:
    """Overridable dependency: None unless an LLM is enabled and keyed."""
    if settings.llm_enabled and settings.anthropic_api_key:
        return extractor.AnthropicClient(settings.anthropic_api_key, settings.llm_model)
    return None


def get_ocr_provider(settings: Settings = Depends(get_settings)) -> OCRProvider | None:
    return get_ocr(settings)


def membership(db: Session, user: User, company_id: int) -> CompanyMember:
    m = db.scalar(select(CompanyMember).where(CompanyMember.company_id == company_id, CompanyMember.user_id == user.id))
    if not m:  # same response as a missing company: do not reveal which ids exist
        raise HTTPException(404, "Kompaniya topilmadi")
    return m


def company_for(db: Session, user: User, company_id: int, write: bool = False, owner: bool = False) -> Company:
    m = membership(db, user, company_id)
    if owner and m.role != "owner":
        raise HTTPException(403, "Faqat kompaniya egasi bajara oladi")
    if write and m.role not in WRITE_ROLES:
        raise HTTPException(403, "Sizda o'zgartirish huquqi yo'q (faqat ko'rish)")
    return m.company


def tender_for(db: Session, user: User, tender_id: int, write: bool = False) -> Tender:
    t = db.get(Tender, tender_id)
    if not t:
        raise HTTPException(404, "Tender topilmadi")
    membership_check = company_for(db, user, t.company_id, write=write)
    assert membership_check.id == t.company_id
    return t


def child_for(db: Session, user: User, model: type, obj_id: int, write: bool = True):  # noqa: ANN201
    obj = db.get(model, obj_id)
    if not obj:
        raise HTTPException(404, "Topilmadi")
    company_for(db, user, obj.company_id, write=write)
    return obj


__all__ = ["current_user", "get_llm", "get_ocr_provider", "company_for", "tender_for", "child_for", "get_session"]
