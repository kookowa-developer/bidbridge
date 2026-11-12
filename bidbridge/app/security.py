from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.models import User

_bearer = HTTPBearer(auto_error=False)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(_N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


def validate_email(email: str) -> str:
    e = email.strip().lower()
    if not _EMAIL.match(e) or len(e) > 254:
        raise HTTPException(422, "Email noto'g'ri")
    return e


def validate_password(pw: str) -> None:
    if len(pw) < 8 or pw.isdigit() or pw.isalpha():
        raise HTTPException(422, "Parol kamida 8 belgi bo'lib, harf va raqamni o'z ichiga olishi kerak")


def create_token(user_id: int, settings: Settings) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "iat": now, "exp": now + timedelta(hours=settings.token_ttl_hours)}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> User:
    if not creds:
        raise HTTPException(401, "Kirish talab qilinadi", headers={"WWW-Authenticate": "Bearer"})
    try:
        data = jwt.decode(creds.credentials, settings.secret_key, algorithms=["HS256"])
        user = db.get(User, int(data["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Token yaroqsiz yoki muddati tugagan", headers={"WWW-Authenticate": "Bearer"}) from None
    if not user:
        raise HTTPException(401, "Foydalanuvchi topilmadi")
    return user


class LoginLimiter:
    """Best-effort brute-force throttle. In-memory: per process, so on serverless it only slows attackers down."""

    def __init__(self) -> None:
        self._fails: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, window: int) -> deque[float]:
        q, cutoff = self._fails[key], time.monotonic() - window
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def check(self, key: str, settings: Settings) -> None:
        if len(self._prune(key, settings.login_window_seconds)) >= settings.login_max_failures:
            raise HTTPException(429, "Juda ko'p urinish. Birozdan so'ng qayta urinib ko'ring")

    def fail(self, key: str, settings: Settings) -> None:
        self._prune(key, settings.login_window_seconds).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)


limiter = LoginLimiter()


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()
