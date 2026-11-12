from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers import auth, ops, passport, tenders

PUBLIC = Path(__file__).resolve().parent.parent / "public"

get_settings().assert_production_safe()
app = FastAPI(title="BidBridge AI", version="0.2.0")
for r in (auth.router, passport.router, tenders.router, ops.router):
    app.include_router(r)


@app.middleware("http")
async def security_headers(request, call_next):  # noqa: ANN001, ANN201
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


# Locally FastAPI serves the UI; on Vercel the same file is served from the CDN (public/).
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(PUBLIC / "index.html")


if PUBLIC.is_dir():
    app.mount("/public", StaticFiles(directory=PUBLIC), name="public")
