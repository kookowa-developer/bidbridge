from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.models import User
from app.security import (
    create_token, current_user, hash_password, limiter, validate_email, validate_password, verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
_DUMMY_HASH = hash_password("not-a-real-password-1")  # constant-time-ish login for unknown emails


class Credentials(BaseModel):
    email: str
    password: str
    full_name: str | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeOut(BaseModel):
    id: int
    email: str
    full_name: str | None
    telegram_linked: bool
    digest_enabled: bool


def _me(u: User) -> MeOut:
    return MeOut(id=u.id, email=u.email, full_name=u.full_name, telegram_linked=u.telegram_chat_id is not None,
                 digest_enabled=u.digest_enabled)


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: Credentials, db: Session = Depends(get_session), settings: Settings = Depends(get_settings)) -> TokenOut:
    if not settings.allow_registration:
        raise HTTPException(403, "Ro'yxatdan o'tish yopiq")
    email = validate_email(body.email)
    validate_password(body.password)
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "Bu email allaqachon ro'yxatdan o'tgan")
    u = User(email=email, password_hash=hash_password(body.password), full_name=(body.full_name or None))
    db.add(u)
    db.commit()
    return TokenOut(access_token=create_token(u.id, settings))


@router.post("/login", response_model=TokenOut)
def login(body: Credentials, request: Request, db: Session = Depends(get_session),
          settings: Settings = Depends(get_settings)) -> TokenOut:
    email = body.email.strip().lower()
    key = f"{email}|{request.client.host if request.client else ''}"
    limiter.check(key, settings)
    u = db.scalar(select(User).where(User.email == email))
    ok = verify_password(body.password, u.password_hash if u else _DUMMY_HASH)
    if not u or not ok:
        limiter.fail(key, settings)
        raise HTTPException(401, "Email yoki parol noto'g'ri")
    limiter.reset(key)
    return TokenOut(access_token=create_token(u.id, settings))


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(current_user)) -> MeOut:
    return _me(user)


@router.put("/digest", response_model=MeOut)
def set_digest(enabled: bool, user: User = Depends(current_user), db: Session = Depends(get_session)) -> MeOut:
    user.digest_enabled = enabled
    db.commit()
    return _me(user)
