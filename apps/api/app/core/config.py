from __future__ import annotations

import json
from typing import Union

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    APP_ENV: str = "development"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://mm_user:mm_password@localhost:5432/mm_ecommerce"

    # Security
    SECRET_KEY: str = "change-me-in-production-use-a-long-random-string-here"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    PASSWORD_RESET_EXPIRE_MINUTES: int = 60   # 1 hour

    # CORS — accepts JSON array string or comma-separated
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    # Allowed hosts — override in production: ["meltingmomentscakes.com", ...]
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

    # Stripe
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""

    # Resend (email)
    RESEND_API_KEY: str = ""
    FROM_EMAIL: str = "orders@meltingmomentscakes.com"

    # Cloudflare R2
    CLOUDFLARE_R2_ACCESS_KEY: str = ""
    CLOUDFLARE_R2_SECRET_KEY: str = ""
    CLOUDFLARE_R2_BUCKET: str = "mm-ecommerce"
    CLOUDFLARE_R2_ENDPOINT: str = ""
    CLOUDFLARE_R2_PUBLIC_URL: str = ""

    # Tabby
    TABBY_API_KEY: str = ""
    TABBY_PUBLIC_KEY: str = ""
    TABBY_MERCHANT_CODE: str = ""

    # Tamara
    TAMARA_API_KEY: str = ""
    TAMARA_API_URL: str = "https://api.tamara.co"

    # Frontend URLs
    WEB_URL: str = "http://localhost:3000"
    ADMIN_URL: str = "http://localhost:3001"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"


settings = Settings()
