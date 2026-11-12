from __future__ import annotations

import hmac
import statistics
from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas as s
from app.config import Settings, get_settings
from app.db import get_session
from app.demo import demo_tender_text, seed_demo
from app.deps import get_llm, get_ocr_provider
from app.models import Company, CompanyMember, PilotTiming, Tender, User
from app.security import current_user
from app.services import extractor, telegram as tgsvc
from app.services.ingest import parse_text
from app.services.ocr import OCRProvider
from app.services.pipeline import Source, create_tender

router = APIRouter(prefix="/api", tags=["ops"])
TARGET_PCT = 50.0


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/demo/seed")
def demo_seed(db: Session = Depends(get_session), user: User = Depends(current_user),
              llm: extractor.LLMClient | None = Depends(get_llm)) -> dict[str, int]:
    company_id, deadline = seed_demo(db, user)
    company = db.get(Company, company_id)
    t = create_tender(db, company, "Maktab mebellari yetkazib berish", "Qashqadaryo XTB",
                      Source(parse_text(demo_tender_text(deadline)), None, "demo.txt"), llm)
    return {"company_id": company_id, "tender_id": t.id}


@router.get("/metrics", response_model=s.MetricsOut)
def metrics(db: Session = Depends(get_session), user: User = Depends(current_user)) -> s.MetricsOut:
    ids = [r for r in db.scalars(select(CompanyMember.company_id).where(CompanyMember.user_id == user.id))]
    tenders = list(db.scalars(select(Tender).where(Tender.company_id.in_(ids)))) if ids else []
    timings = list(db.scalars(select(PilotTiming).join(Tender).where(Tender.company_id.in_(ids)))) if ids else []
    secs = [t.analysis_seconds for t in tenders if t.analysis_seconds is not None]
    red = [100 * (1 - p.assisted_minutes / p.manual_minutes) for p in timings]
    n = len(red)
    return s.MetricsOut(
        tenders_analysed=len(tenders),
        avg_analysis_seconds=round(sum(secs) / len(secs), 2) if secs else None,
        ocr_runs=sum(1 for t in tenders if t.ocr_provider),
        pilot_samples=n,
        mean_reduction_pct=round(statistics.fmean(red), 1) if red else None,
        median_reduction_pct=round(statistics.median(red), 1) if red else None,
        min_reduction_pct=round(min(red), 1) if red else None,
        max_reduction_pct=round(max(red), 1) if red else None,
        target_pct=TARGET_PCT,
        target_met=(statistics.median(red) >= TARGET_PCT) if n >= 10 else None,
        note="Maqsad bajarilgan deb hisoblanishi uchun kamida 10 ta o'lchov va median ≥ 50% kerak. "
             "Vaqtlar foydalanuvchi kiritgan qiymatlar.",
    )


# ---------------------------------------------------------------- Telegram
@router.post("/telegram/link-code", response_model=s.TelegramLinkOut)
def link_code(db: Session = Depends(get_session), user: User = Depends(current_user),
              settings: Settings = Depends(get_settings)) -> s.TelegramLinkOut:
    code = tgsvc.new_link_code(db, user)
    bot = settings.telegram_bot_username
    return s.TelegramLinkOut(code=code, deep_link=f"https://t.me/{bot}?start={code}" if bot else None,
                             expires_minutes=15, bot_configured=bool(settings.telegram_bot_token))


def get_telegram(settings: Settings = Depends(get_settings)) -> tgsvc.TelegramAPI:
    if not settings.telegram_bot_token:
        raise HTTPException(503, "Telegram bot sozlanmagan")
    return tgsvc.HttpTelegram(settings.telegram_bot_token)


@router.post("/telegram/webhook", include_in_schema=False)
def webhook(
    update: dict,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    tg: tgsvc.TelegramAPI = Depends(get_telegram),
    llm: extractor.LLMClient | None = Depends(get_llm),
    ocr: OCRProvider | None = Depends(get_ocr_provider),
    x_telegram_bot_api_secret_token: str | None = Header(None),
) -> dict[str, bool]:
    secret = settings.telegram_webhook_secret
    if not secret or not x_telegram_bot_api_secret_token or not hmac.compare_digest(secret, x_telegram_bot_api_secret_token):
        raise HTTPException(403, "Forbidden")
    tgsvc.handle_update(db, update, tg, settings, llm, ocr)
    return {"ok": True}  # always 200 so Telegram does not retry-storm on handled errors


@router.get("/cron/digest", include_in_schema=False)
def cron_digest(
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    tg: tgsvc.TelegramAPI = Depends(get_telegram),
    authorization: str | None = Header(None),
) -> dict[str, int]:
    expected = f"Bearer {settings.cron_secret}" if settings.cron_secret else None
    if not expected or not authorization or not hmac.compare_digest(expected, authorization):
        raise HTTPException(401, "Unauthorized")
    sent = 0
    for chat_id, text in tgsvc.build_digests(db, settings, date.today()):
        try:
            tg.send_message(chat_id, text)
            sent += 1
        except Exception:  # one blocked user must not stop the rest
            continue
    return {"sent": sent}
