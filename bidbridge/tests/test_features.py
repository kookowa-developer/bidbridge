from __future__ import annotations

import io
import shutil
from datetime import date, timedelta

import pytest

from app.config import get_settings
from app.deps import get_llm, get_ocr_provider
from app.domain import Status
from app.services import bid as bidmod
from app.services import compliance as comp
from app.services.ocr import split_pages


# ------------------------------------------------------------------ Bid / No-Bid
def _decide(results, cons=(), revenue=None, deadline=date(2026, 12, 25), today=date(2026, 11, 12), security=None):
    snap = comp.CompanySnap("X", revenue_by_year=revenue or {})
    r = comp.readiness(list(results) + list(cons))
    return bidmod.decide(list(results), list(cons), r, snap, deadline, today, security)


def _res(status, type_="document", generate=None):
    return comp.CheckResult(None, type_, "L", status, "m", generate=generate)


def test_bid_go_conditional_nogo():
    ok = [_res(Status.OK) for _ in range(6)]
    assert _decide(ok, revenue={2025: 1e9}, security=5e6).recommendation == "GO"
    mixed = ok[:3] + [_res(Status.FAIL), _res(Status.WARN), _res(Status.MANUAL, "security")]
    d = _decide(mixed, revenue={2025: 1e9}, deadline=date(2026, 11, 22))  # 10 days for ~9 days of work
    assert d.recommendation == "CONDITIONAL" and d.effort_days == 9.0, (d.score, d.effort_days)
    blocker = _decide(ok + [_res(Status.FAIL, "numeric")])
    assert blocker.recommendation == "NO-GO" and blocker.blockers
    assert _decide(ok, deadline=date(2026, 11, 1)).recommendation == "NO-GO"  # deadline passed


def test_bid_time_pressure_and_financial_exposure_lower_the_score():
    items = [_res(Status.FAIL) for _ in range(3)] + [_res(Status.OK) for _ in range(5)]
    relaxed = _decide(items, deadline=date(2027, 3, 1))
    rushed = _decide(items, deadline=date(2026, 11, 15))
    assert rushed.score < relaxed.score
    light = _decide([_res(Status.OK)], revenue={2025: 1e9}, security=5e6)
    heavy = _decide([_res(Status.OK)], revenue={2025: 1e9}, security=2e8)
    assert heavy.score < light.score
    f = {x.name: x for x in heavy.factors}["Moliyaviy yuk (taklif ta'minoti)"]
    assert f.score < 30 and abs(sum(x.weight for x in heavy.factors) - 1.0) < 1e-9


def test_bid_endpoint_on_demo(client):
    ids = client.post("/api/demo/seed").json()
    d = client.get(f"/api/tenders/{ids['tender_id']}/bid").json()
    assert d["recommendation"] == "NO-GO"  # 450 < 500 is a capability gap
    assert any("450" in b for b in d["blockers"]) and len(d["factors"]) == 5 and d["assumptions"]


# ------------------------------------------------------------------ OCR
class FakeOCR:
    name = "fake"

    def __init__(self):
        self.calls = []

    def ocr_pdf(self, data, page_numbers):
        self.calls.append(page_numbers)
        return {n: "Ishtirokchi kamida 7 yil faoliyat yuritgan bo'lishi kerak." for n in page_numbers}

    def ocr_image(self, data):
        return "Taklif ta'minoti 9 000 000 so'm bo'lishi kerak."


def _blank_pdf() -> bytes:
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=300, height=300)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_scanned_pdf_uses_ocr_and_records_provider(client):
    from app.main import app
    fake = FakeOCR()
    app.dependency_overrides[get_ocr_provider] = lambda: fake
    cid = client.post("/api/companies", json={"legal_name": "Alfa MChJ"}).json()["id"]
    r = client.post(f"/api/companies/{cid}/tenders", data={"title": "Scan"}, files={"file": ("scan.pdf", _blank_pdf())})
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["ocr_provider"] == "fake" and fake.calls == [[1]]
    assert t["requirements"][0]["value"] == 7 and t["requirements"][0]["page"] == 1
    img = client.post(f"/api/companies/{cid}/tenders", data={"title": "Img"}, files={"file": ("p.png", b"\x89PNG....")})
    assert img.status_code == 201 and img.json()["requirements"][0]["type"] == "security"


def test_scanned_pdf_without_ocr_gives_actionable_error(client):
    cid = client.post("/api/companies", json={"legal_name": "Alfa MChJ"}).json()["id"]
    r = client.post(f"/api/companies/{cid}/tenders", data={"title": "Scan"}, files={"file": ("scan.pdf", _blank_pdf())})
    assert r.status_code == 422 and "OCR" in r.json()["detail"]
    r = client.post(f"/api/companies/{cid}/tenders", data={"title": "Img"}, files={"file": ("p.png", b"x")})
    assert r.status_code == 422 and "OCR" in r.json()["detail"]


def test_ocr_failure_does_not_crash(client):
    from app.main import app

    class Broken(FakeOCR):
        def ocr_pdf(self, data, page_numbers):
            raise RuntimeError("engine down")

    app.dependency_overrides[get_ocr_provider] = lambda: Broken()
    cid = client.post("/api/companies", json={"legal_name": "Alfa MChJ"}).json()["id"]
    r = client.post(f"/api/companies/{cid}/tenders", data={"title": "Scan"}, files={"file": ("scan.pdf", _blank_pdf())})
    assert r.status_code == 422


def test_homoglyph_repair_keeps_real_cyrillic():
    from app.services.ocr import fix_homoglyphs
    assert fix_homoglyphs("must have \u0430 least 5") == "must have a least 5"
    assert fix_homoglyphs("Тўлов муддати") == "Тўлов муддати"


def test_split_pages_parser():
    assert split_pages("=== PAGE 1 ===\nabc\n=== PAGE 3 ===\nxyz\n") == {1: "abc", 3: "xyz"}


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_real_tesseract_reads_a_scanned_pdf(monkeypatch):
    from PIL import Image, ImageDraw, ImageFont
    from app.services.ocr import TesseractOCR
    from app.services.pipeline import read_source
    from app.services.extractor import extract_rules

    font = ImageFont.load_default(size=56)
    img = Image.new("RGB", (2400, 300), "white")
    ImageDraw.Draw(img).text((40, 90), "Participants must have at least 5 years of experience.", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=250.0)  # image-only PDF == a scan: no text layer
    src = read_source("scan.pdf", buf.getvalue(), None, TesseractOCR())
    assert src.ocr_provider == "tesseract"
    reqs = extract_rules(src.pages)
    assert any(r.type.value == "experience" and r.value == 5 for r in reqs), src.pages[0].text


# ------------------------------------------------------------------ Telegram
class FakeTG:
    def __init__(self, files=None):
        self.sent = []
        self.files = files or {}

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    def download_file(self, file_id):
        return self.files[file_id]


@pytest.fixture()
def tg(client):
    from app.main import app
    from app.routers.ops import get_telegram
    s = get_settings()
    s.telegram_webhook_secret, s.cron_secret = "whsec", "cronsec"
    fake = FakeTG()
    app.dependency_overrides[get_telegram] = lambda: fake
    yield fake
    s.telegram_webhook_secret = s.cron_secret = None


def _send(client, text=None, chat=555, secret="whsec", **extra):
    msg = {"chat": {"id": chat}, **extra}
    if text is not None:
        msg["text"] = text
    return client.post("/api/telegram/webhook", json={"message": msg}, headers={"X-Telegram-Bot-Api-Secret-Token": secret})


def test_telegram_webhook_requires_secret(client, tg):
    assert _send(client, "/help", secret="nope").status_code == 403
    assert client.post("/api/telegram/webhook", json={"message": {"chat": {"id": 1}, "text": "/help"}}).status_code == 403
    assert tg.sent == []


def test_telegram_full_flow(client, tg):
    client.post("/api/demo/seed")
    _send(client, "/alerts")
    assert "ulang" in tg.sent[-1][1]  # unlinked users are told to link first
    code = client.post("/api/telegram/link-code").json()["code"]
    assert _send(client, "/start WRONG000").status_code == 200 and "noto'g'ri" in tg.sent[-1][1]
    _send(client, f"/start {code}")
    assert "Hisob ulandi" in tg.sent[-1][1] and client.get("/api/auth/me").json()["telegram_linked"] is True
    _send(client, f"/start {code}")
    assert "noto'g'ri" in tg.sent[-1][1]  # codes are single-use

    _send(client, "/alerts")
    assert "muddati" in tg.sent[-1][1]
    _send(client, "/tenders")
    assert "Maktab mebellari" in tg.sent[-1][1]
    _send(client, "/check")
    check = tg.sent[-1][1]
    assert "TAYYOR EMAS" in check and "450" in check
    _send(client, "/bid")
    assert "NO-GO" in tg.sent[-1][1] and "To'siqlar" in tg.sent[-1][1]
    _send(client, "/bid 99999")
    assert "topilmadi" in tg.sent[-1][1]

    long_text = "Ishtirokchi kamida 4 yil faoliyat yuritgan bo'lishi kerak. " * 5
    _send(client, long_text)
    assert any("Tahlil qilinmoqda" in m for _, m in tg.sent) and "Telegram orqali" in tg.sent[-1][1]
    docx_bytes = io.BytesIO()
    from docx import Document
    d = Document(); d.add_paragraph("Taklif ta'minoti 5 000 000 so'm bo'lishi kerak."); d.save(docx_bytes)
    tg.files["f1"] = docx_bytes.getvalue()
    _send(client, None, document={"file_id": "f1", "file_name": "tender.docx", "file_size": 1000})
    assert "tender" in tg.sent[-1][1]
    _send(client, None, document={"file_id": "f1", "file_name": "huge.pdf", "file_size": 50 * 1024 * 1024})
    assert "juda katta" in tg.sent[-1][1]
    _send(client, "salom")
    assert "Tushunmadim" in tg.sent[-1][1]


def test_telegram_ignores_malformed_updates(client, tg):
    assert client.post("/api/telegram/webhook", json={"callback_query": {}}, headers={"X-Telegram-Bot-Api-Secret-Token": "whsec"}).status_code == 200
    assert client.post("/api/telegram/webhook", json={"message": {"text": "x"}}, headers={"X-Telegram-Bot-Api-Secret-Token": "whsec"}).status_code == 200


def test_cron_digest_auth_and_content(client, tg):
    assert client.get("/api/cron/digest").status_code == 401
    assert client.get("/api/cron/digest", headers={"Authorization": "Bearer wrong"}).status_code == 401
    client.post("/api/demo/seed")
    code = client.post("/api/telegram/link-code").json()["code"]
    _send(client, f"/start {code}")
    tg.sent.clear()
    r = client.get("/api/cron/digest", headers={"Authorization": "Bearer cronsec"})
    assert r.json() == {"sent": 1} and tg.sent[0][0] == 555 and "Kunlik xulosa" in tg.sent[0][1]
    client.put("/api/auth/digest", params={"enabled": False})
    assert client.get("/api/cron/digest", headers={"Authorization": "Bearer cronsec"}).json() == {"sent": 0}


def test_telegram_not_configured_returns_503(client):
    from app.main import app
    app.dependency_overrides.pop(__import__("app.routers.ops", fromlist=["get_telegram"]).get_telegram, None)
    assert client.get("/api/cron/digest", headers={"Authorization": "Bearer x"}).status_code == 503


# ------------------------------------------------------------------ Metrics / pilot evidence
def test_pilot_metrics_and_target(client):
    ids = client.post("/api/demo/seed").json()
    tid = ids["tender_id"]
    m = client.get("/api/metrics").json()
    assert m["tenders_analysed"] == 1 and m["pilot_samples"] == 0 and m["mean_reduction_pct"] is None
    assert m["avg_analysis_seconds"] is not None and m["target_met"] is None
    assert client.post(f"/api/tenders/{tid}/pilot", json={"manual_minutes": 0, "assisted_minutes": 5}).status_code == 422
    for manual, assisted in [(120, 40)] * 5 + [(90, 30)] * 5:
        assert client.post(f"/api/tenders/{tid}/pilot", json={"manual_minutes": manual, "assisted_minutes": assisted}).status_code == 201
    m = client.get("/api/metrics").json()
    assert m["pilot_samples"] == 10 and m["median_reduction_pct"] == 66.7 and m["target_met"] is True
    client.post(f"/api/tenders/{tid}/pilot", json={"manual_minutes": 100, "assisted_minutes": 95})
    assert client.get("/api/metrics").json()["min_reduction_pct"] == 5.0


# ------------------------------------------------------------------ extraction benchmark as a regression gate
def test_benchmark_corpus_regression_gate():
    from pathlib import Path
    from app.evalkit import run
    rep = run(Path(__file__).resolve().parent.parent / "eval" / "corpus")
    assert rep.overall["f1"] >= 0.9, rep.cases
    assert rep.quote_verification == 1.0 and rep.deadline_accuracy == 1.0
