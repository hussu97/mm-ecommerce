from __future__ import annotations

import json
from typing import Union

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Sentinel values that must NOT reach production
_DEV_DATABASE_URL = (
    "postgresql+asyncpg://mm_user:mm_password@localhost:5432/mm_ecommerce"
)
_DEV_SECRET_KEY = "change-me-in-production-use-a-long-random-string-here"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── App ──────────────────────────────────────────────────────────────────
    APP_ENV: str = "development"
    USE_SSL: bool = False

    # ── Database ─────────────────────────────────────────────────────────────
    DATABASE_URL: str = _DEV_DATABASE_URL

    # ── Security ─────────────────────────────────────────────────────────────
    SECRET_KEY: str = _DEV_SECRET_KEY
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    PASSWORD_RESET_EXPIRE_MINUTES: int = 60

    # ── CORS ─────────────────────────────────────────────────────────────────
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:3001"]
    ALLOWED_HOSTS: list[str] = ["*"]

    @field_validator("ALLOWED_HOSTS", mode="before")
    @classmethod
    def parse_allowed_hosts(cls, v: Union[str, list]) -> list[str]:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [h.strip() for h in v.split(",")]
        return v

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors(cls, v: Union[str, list]) -> list[str]:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [origin.strip() for origin in v.split(",")]
        return v

    # ── Stripe ────────────────────────────────────────────────────────────────
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""

    # ── Resend (email) ────────────────────────────────────────────────────────
    RESEND_API_KEY: str = ""
    FROM_EMAIL: str = "orders@meltingmomentscakes.com"

    # ── Cloudflare R2 (object storage) ───────────────────────────────────────
    CLOUDFLARE_R2_ACCESS_KEY: str = ""
    CLOUDFLARE_R2_SECRET_KEY: str = ""
    CLOUDFLARE_R2_BUCKET: str = "melting-moments-cakes"
    CLOUDFLARE_R2_ENDPOINT: str = ""
    CLOUDFLARE_R2_PUBLIC_URL: str = ""

    # ── Tabby (BNPL) ─────────────────────────────────────────────────────────
    TABBY_API_KEY: str = ""
    TABBY_PUBLIC_KEY: str = ""
    TABBY_MERCHANT_CODE: str = ""

    # ── Tamara (BNPL) ────────────────────────────────────────────────────────
    TAMARA_API_KEY: str = ""
    TAMARA_API_URL: str = "https://api.tamara.co"

    # ── Frontend URLs (email templates & CORS) ────────────────────────────────
    WEB_URL: str = "http://localhost:3000"
    ADMIN_URL: str = "http://localhost:3001"

    # ── Backups ───────────────────────────────────────────────────────────────
    BACKUP_GCS_BUCKET: str = ""

    # ── Redis (optional — leave empty to disable caching) ────────────────────
    REDIS_URL: str = ""

    # ── Umami Cloud analytics (optional — leave empty to disable) ────────────
    UMAMI_API_KEY: str = ""
    UMAMI_WEBSITE_ID: str = ""

    # ── Sentry (optional — leave empty to disable error tracking) ────────────
    SENTRY_DSN: str = ""
    SENTRY_ENVIRONMENT: str = "development"

    # ── Production guard ──────────────────────────────────────────────────────
    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        """Fail fast on startup if any required secret is missing in production."""
        if self.APP_ENV != "production":
            return self

        errors: list[str] = []

        # Core — app cannot function without these
        if self.DATABASE_URL == _DEV_DATABASE_URL:
            errors.append(
                "DATABASE_URL is still the dev default — set a real production URL"
            )
        if self.SECRET_KEY == _DEV_SECRET_KEY:
            errors.append(
                "SECRET_KEY is still the placeholder — generate one with: "
                "openssl rand -hex 32"
            )

        # Integrations — required for a functioning storefront
        required: dict[str, str] = {
            "STRIPE_SECRET_KEY": self.STRIPE_SECRET_KEY,
            "STRIPE_WEBHOOK_SECRET": self.STRIPE_WEBHOOK_SECRET,
            "RESEND_API_KEY": self.RESEND_API_KEY,
            "CLOUDFLARE_R2_ACCESS_KEY": self.CLOUDFLARE_R2_ACCESS_KEY,
            "CLOUDFLARE_R2_SECRET_KEY": self.CLOUDFLARE_R2_SECRET_KEY,
            "CLOUDFLARE_R2_ENDPOINT": self.CLOUDFLARE_R2_ENDPOINT,
            "CLOUDFLARE_R2_PUBLIC_URL": self.CLOUDFLARE_R2_PUBLIC_URL,
        }
        for name, value in required.items():
            if not value:
                errors.append(f"{name} is required in production but is not set")

        if errors:
            formatted = "\n  • ".join(errors)
            raise ValueError(
                f"Production configuration errors — fix these before starting:\n"
                f"  • {formatted}"
            )

        return self

    # ── Helpers ───────────────────────────────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"


settings = Settings()
