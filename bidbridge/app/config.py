from __future__ import annotations

import logging
import os
from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)
_DEV_SECRET = "dev-insecure-secret-change-me"


class Settings(BaseSettings):
    """Runtime configuration. Every value can be overridden with a BIDBRIDGE_* env var."""

    model_config = SettingsConfigDict(env_prefix="BIDBRIDGE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./bidbridge.db"

    # --- security
    secret_key: str = _DEV_SECRET
    token_ttl_hours: int = 24 * 7
    allow_registration: bool = True
    login_max_failures: int = 5
    login_window_seconds: int = 300

    # --- analysis
    llm_enabled: bool = False
    anthropic_api_key: str | None = None
    llm_model: str = "claude-sonnet-5-5"
    llm_max_chars_per_call: int = 60_000
    ocr_provider: str = "auto"  # auto | tesseract | anthropic | none
    ocr_max_pages: int = 30

    # --- limits (Vercel request bodies are capped at ~4.5 MB)
    max_upload_mb: int = 4
    expiry_warn_days: int = 30
    risky_expiry_margin_days: int = 14

    # --- Telegram / cron
    telegram_bot_token: str | None = None
    telegram_bot_username: str | None = None
    telegram_webhook_secret: str | None = None
    # Vercel injects CRON_SECRET and sends it as a Bearer token on cron calls, so accept that name too.
    cron_secret: str | None = Field(default=None, validation_alias=AliasChoices("BIDBRIDGE_CRON_SECRET", "CRON_SECRET"))

    @field_validator("database_url")
    @classmethod
    def _normalise_pg(cls, v: str) -> str:
        # Neon / Vercel Postgres hand out postgres:// or postgresql:// URLs; we use psycopg v3.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v

    def assert_production_safe(self) -> None:
        if self.secret_key == _DEV_SECRET and (os.getenv("VERCEL") or os.getenv("BIDBRIDGE_PRODUCTION")):
            raise RuntimeError("BIDBRIDGE_SECRET_KEY must be set in production")
        if self.secret_key == _DEV_SECRET:
            log.warning("Using the insecure development secret key")


@lru_cache
def get_settings() -> Settings:
    return Settings()
