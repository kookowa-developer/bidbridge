"""Telegram bot logic (webhook mode: works on serverless hosts, no long polling).

Commands: /start <code> links the account, /alerts, /tenders, /check [id], /bid [id], /help.
Sending a PDF/DOCX/TXT/image (or a long text message) analyses it as a new tender.
"""
from __future__ import annotations

import logging
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Company, CompanyMember, Tender, User
from app.security import hash_code
from app.services import extractor
from app.services.deadlines import build_alerts
from app.services.ocr import OCRProvider
from app.services.pipeline import EmptyDocument, create_tender, read_source
from app.services.reports import bid_decision, build_report

log = logging.getLogger(__name__)
ICON = {"ok": "✅", "warn": "⚠️", "fail": "❌", "manual": "🔎"}
HELP = (
    "BidBridge AI bot\n"
    "/alerts — muddatlar va ogohlantirishlar\n"
    "/tenders — oxirgi tenderlar\n"
    "/check [id] — tenderning moslik hisoboti\n"
    "/bid [id] — Bid/No-Bid tavsiyasi\n"
    "Tender hujjatini (PDF, DOCX, TXT, rasm) yuboring: tahlil qilib beraman."
)


class TelegramAPI(Protocol):
    def send_message(self, chat_id: int, text: str) -> None: ...

    def download_file(self, file_id: str) -> bytes: ...


class HttpTelegram:
    def __init__(self, token: str) -> None:
        self._base = f"https://api.telegram.org/bot{token}"
        self._file_base = f"https://api.telegram.org/file/bot{token}"

    def send_message(self, chat_id: int, text: str) -> None:
        for i in range(0, len(text), 3900):  # Telegram limit is 4096 chars
            r = httpx.post(f"{self._base}/sendMessage", json={"chat_id": chat_id, "text": text[i:i + 3900],
                                                              "disable_web_page_preview": True}, timeout=15)
            r.raise_for_status()

    def download_file(self, file_id: str) -> bytes:
        info = httpx.get(f"{self._base}/getFile", params={"file_id": file_id}, timeout=15)
        info.raise_for_status()
        path = info.json()["result"]["file_path"]
        r = httpx.get(f"{self._file_base}/{path}", timeout=60)
        r.raise_for_status()
        return r.content


def new_link_code(db: Session, user: User, ttl_minutes: int = 15) -> str:
    code = secrets.token_hex(4).upper()
    user.tg_link_hash = hash_code(code)
    user.tg_link_expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    db.commit()
    return code


def _user_for_chat(db: Session, chat_id: int) -> User | None:
    return db.scalar(select(User).where(User.telegram_chat_id == chat_id))


def _companies(db: Session, user: User) -> list[Company]:
    return list(db.scalars(select(Company).join(CompanyMember).where(CompanyMember.user_id == user.id).order_by(Company.id)))


def _latest_tender(db: Session, user: User, tender_id: int | None) -> Tender | None:
    ids = [c.id for c in _companies(db, user)]
    q = select(Tender).where(Tender.company_id.in_(ids))
    q = q.where(Tender.id == tender_id) if tender_id else q.order_by(Tender.id.desc())
    return db.scalars(q).first() if ids else None


def format_report(t: Tender, rep, today: date) -> str:  # noqa: ANN001
    r = rep.readiness
    lines = [f"📄 {t.title}", f"Tayyorlik: {r.score}% — {r.verdict}",
             f"Muddat: {t.deadline:%d.%m.%Y}" if t.deadline else "Muddat aniqlanmadi", ""]
    order = {"fail": 0, "warn": 1, "manual": 2, "ok": 3}
    for x in sorted(rep.results + rep.consistency, key=lambda x: order[x.status.value])[:15]:
        if x.status.value == "ok":
            continue
        lines.append(f"{ICON[x.status.value]} {x.label}: {x.message}")
        if x.action:
            lines.append(f"   → {x.action}")
    if len(lines) <= 4:
        lines.append("✅ Barcha tekshiruvlar muvaffaqiyatli.")
    lines.append(f"\nBatafsil: /bid {t.id}")
    return "\n".join(lines)


def handle_update(
    db: Session, update: dict, tg: TelegramAPI, settings: Settings,
    llm: extractor.LLMClient | None = None, ocr: OCRProvider | None = None,
) -> None:
    msg = update.get("message") or update.get("edited_message")
    if not msg or "chat" not in msg:
        return
    chat_id = int(msg["chat"]["id"])
    text = (msg.get("text") or msg.get("caption") or "").strip()
    today = date.today()

    def say(s: str) -> None:
        try:
            tg.send_message(chat_id, s)
        except Exception:
            log.exception("telegram send failed")

    # ---- account linking
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) == 2:
            code = parts[1].strip().upper()
            user = db.scalar(select(User).where(User.tg_link_hash == hash_code(code)))
            exp = user.tg_link_expires if user else None
            if exp and exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if not user or not exp or exp < datetime.now(timezone.utc):
                return say("Kod noto'g'ri yoki muddati tugagan. Saytdagi «Telegram» bo'limidan yangi kod oling.")
            other = _user_for_chat(db, chat_id)
            if other and other.id != user.id:
                other.telegram_chat_id = None
                db.flush()
            user.telegram_chat_id, user.tg_link_hash, user.tg_link_expires = chat_id, None, None
            db.commit()
            return say(f"Hisob ulandi: {user.email}\n\n{HELP}")
        return say("Hisobni ulash uchun saytdagi «Telegram» bo'limidan olingan havolani oching.\n\n" + HELP)

    user = _user_for_chat(db, chat_id)
    if not user:
        return say("Avval hisobingizni ulang: saytda Telegram bo'limidan kod oling va /start KOD yuboring.")

    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    arg = arg.strip()
    tid = int(arg) if arg.isdigit() else None

    if cmd in {"/help", "/menu"}:
        return say(HELP)
    if cmd == "/alerts":
        alerts = [a for c in _companies(db, user) for a in build_alerts(c, today, settings.expiry_warn_days)]
        if not alerts:
            return say("Ogohlantirish yo'q ✅")
        icon = {"critical": "🔴", "warning": "🟠", "info": "🔵"}
        return say("\n".join(f"{icon[a.severity]} {a.message}" for a in alerts[:20]))
    if cmd == "/tenders":
        ids = [c.id for c in _companies(db, user)]
        ts = list(db.scalars(select(Tender).where(Tender.company_id.in_(ids)).order_by(Tender.id.desc()).limit(10))) if ids else []
        return say("\n".join(f"#{t.id} {t.title} — muddat: {t.deadline:%d.%m.%Y}" if t.deadline else f"#{t.id} {t.title}" for t in ts)
                   or "Tender yo'q. Hujjat yuboring.")
    if cmd in {"/check", "/bid"}:
        t = _latest_tender(db, user, tid)
        if not t:
            return say("Tender topilmadi.")
        rep = build_report(t, today, settings)
        if cmd == "/check":
            return say(format_report(t, rep, today))
        d = bid_decision(t, rep, today)
        out = [f"🎯 {d.recommendation} ({d.score}/100, ishonch: {d.confidence})", d.summary, ""]
        out += [f"• {f.name}: {round(f.score)}/100 — {f.detail}" for f in d.factors]
        if d.blockers:
            out += ["", "To'siqlar:"] + [f"❌ {b}" for b in d.blockers]
        return say("\n".join(out))

    # ---- document / long text => new tender
    doc = msg.get("document")
    photo = msg.get("photo")
    if doc or photo or (len(text) >= 200 and not text.startswith("/")):
        companies = _companies(db, user)
        if not companies:
            return say("Avval saytda kompaniya yarating.")
        started = time.perf_counter()
        try:
            if doc or photo:
                size = (doc or photo[-1]).get("file_size") or 0
                if size > settings.max_upload_mb * 1024 * 1024:
                    return say(f"Fayl juda katta (chegara {settings.max_upload_mb} MB).")
                file_id = doc["file_id"] if doc else photo[-1]["file_id"]
                name = doc.get("file_name", "tender.pdf") if doc else "photo.jpg"
                src = read_source(name, tg.download_file(file_id), None, ocr)
                title = name.rsplit(".", 1)[0]
            else:
                src = read_source(None, None, text, None)
                title = "Telegram orqali yuborilgan tender"
            say("Tahlil qilinmoqda...")
            t = create_tender(db, companies[0], title, None, src, llm, started)
        except Exception as e:  # keep the bot responsive; surface a friendly message
            log.exception("telegram ingest failed")
            return say(f"Tahlil qilib bo'lmadi: {e}" if isinstance(e, (EmptyDocument, ValueError)) else "Tahlil qilib bo'lmadi.")
        return say(format_report(t, build_report(t, today, settings), today))

    say("Tushunmadim. /help")


def build_digests(db: Session, settings: Settings, today: date | None = None) -> list[tuple[int, str]]:
    """(chat_id, text) for every linked user that has critical/warning alerts."""
    today = today or date.today()
    out: list[tuple[int, str]] = []
    for user in db.scalars(select(User).where(User.telegram_chat_id.is_not(None), User.digest_enabled.is_(True))):
        alerts = [a for c in _companies(db, user) for a in build_alerts(c, today, settings.expiry_warn_days)
                  if a.severity in {"critical", "warning"}]
        if alerts:
            icon = {"critical": "🔴", "warning": "🟠"}
            out.append((user.telegram_chat_id, "Kunlik xulosa:\n" + "\n".join(f"{icon[a.severity]} {a.message}" for a in alerts[:20])))
    return out
