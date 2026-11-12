# BidBridge AI — tender bid readiness (Uzbekistan)

Tender hujjatini (PDF / skaner PDF / rasm / DOCX / matn) o'qiydi, talablarni **iqtibos + sahifa raqami** bilan ajratadi,
kompaniya pasportiga solishtiradi, Bid/No-Bid tavsiyasini beradi, Telegram orqali ham ishlaydi.

## Nima bor
| Modul | Holat |
|---|---|
| Autentifikatsiya (JWT, scrypt, login limiter), ko'p-kompaniyali rollar (owner/lawyer/accountant/technical/viewer) | testlangan |
| Talab ajratish (qoida asosida; ixtiyoriy Claude, iqtibosi topilmasa tashlab yuboriladi) | testlangan, sintetik korpusda F1 = 1.0 |
| Moslik tekshiruvi (muddat tender topshirish sanasiga qarab, hujjatlararo STIR/nom nomuvofiqligi) | testlangan |
| OCR: Tesseract (lokal) yoki Claude vision (Vercel) | Tesseract haqiqiy test; Claude vision **jonli sinalmagan** |
| Bid/No-Bid (shaffof vaznli model, to'siqlar, taxminlar) | testlangan |
| Telegram bot (webhook, /alerts /tenders /check /bid, fayl yuborish) + kunlik cron xulosa | fake API bilan testlangan; **jonli sinalmagan** |
| Pilot o'lchovi (qo'lda vs BidBridge vaqti, median ≥ 50%, ≥ 10 o'lchov) | testlangan |
| Hujjat generatori (.docx shablonlar) | testlangan |

## Lokal ishga tushirish
```bash
pip install -r requirements-dev.txt
python -m pytest -q
uvicorn app.main:app --reload      # http://localhost:8000
```
Docker (Postgres + Tesseract): `cp .env.example .env && docker compose up --build`.

## Vercel'ga deploy (bir marta, ~10 daqiqa)
1. **Neon** (neon.tech) da bepul Postgres oching, connection string'ni nusxalang.
2. Repozitoriyni GitHub'ga push qiling → Vercel'da **Add New → Project → Import**. Framework: *Other* (FastAPI `index.py` avtomatik aniqlanadi).
3. **Environment Variables**:
   - `BIDBRIDGE_SECRET_KEY` = `openssl rand -hex 32`
   - `BIDBRIDGE_DATABASE_URL` = Neon URL
   - `CRON_SECRET` = tasodifiy uzun satr (Vercel cron shuni Bearer sifatida yuboradi)
   - Skaner PDF uchun: `BIDBRIDGE_ANTHROPIC_API_KEY` (Vercel'da Tesseract yo'q)
   - Telegram uchun: `BIDBRIDGE_TELEGRAM_BOT_TOKEN`, `BIDBRIDGE_TELEGRAM_BOT_USERNAME`, `BIDBRIDGE_TELEGRAM_WEBHOOK_SECRET`
4. Deploy. Tekshiring: `https://<loyiha>.vercel.app/api/health` → `{"status":"ok"}`.
5. Telegram webhook (BotFather'dan token olgach):
```bash
curl "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=https://<loyiha>.vercel.app/api/telegram/webhook" -d "secret_token=<WEBHOOK_SECRET>"
```
6. Saytda ro'yxatdan o'ting → «Telegram» bo'limidan kod oling → botga `/start KOD`.

Cheklovlar: Vercel so'rov hajmi ~4.5 MB (shuning uchun yuklash limiti 4 MB); fayllar bazada saqlanadi;
login limiter jarayon-xotirasida (serverless'da kuchsiz — production'da Redis/Upstash'ga o'tkazing).

## Aniqlik va "50% vaqt qisqarishi" — haqiqiy ma'lumot bilan isbotlash
`eval/corpus/` dagi 5 ta tender **men yozgan sintetik namuna** — bu regressiya to'sig'i, haqiqiy aniqlik emas.
1. 5–10 ta real tender hujjatini (UzEx, xt-xarid, banklar, Beeline) `eval/corpus/NN_nom.txt` ga qo'ying (PDF bo'lsa matnini chiqaring yoki `ocr` bilan).
2. Yoniga `NN_nom.json` etalon yorliqlarini yozing (mavjud fayllar namuna).
3. `python -m app.evalkit eval/corpus` — precision/recall/F1, muddat aniqligi, iqtibos tekshiruvi. Xato chiqqan qoidalarni `app/services/extractor.py` da sozlang.
4. Vaqt: har bir tender sahifasidagi «Vaqt o'lchovi» formasiga qo'lda va BidBridge bilan sarflangan daqiqalarni kiriting. «Natijalar (pilot)» da **median** ≥ 50% va ≥ 10 o'lchov bo'lsagina maqsad bajarilgan hisoblanadi.
