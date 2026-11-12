from __future__ import annotations

import os
import threading
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from app.config import get_settings
from app.models import Base


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        kwargs: dict[str, object] = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url or url == "sqlite://":
            kwargs["poolclass"] = StaticPool
        return create_engine(url, **kwargs)
    if os.getenv("VERCEL"):  # serverless: let the provider's pooler (e.g. Neon pgbouncer) do the pooling
        return create_engine(url, poolclass=NullPool)
    return create_engine(url, pool_pre_ping=True)


engine: Engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

_schema_lock = threading.Lock()
_schema_ready = False


def init_db(bind: Engine | None = None) -> None:
    Base.metadata.create_all(bind or engine)


def ensure_schema() -> None:
    """Create tables lazily on first use (no startup hook needed on serverless hosts)."""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if not _schema_ready:
            init_db()
            _schema_ready = True


def reset_schema_flag() -> None:
    global _schema_ready
    _schema_ready = False


def get_session() -> Iterator[Session]:
    ensure_schema()
    with SessionLocal() as session:
        yield session
