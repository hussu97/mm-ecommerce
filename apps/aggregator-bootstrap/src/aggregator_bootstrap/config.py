"""Settings for the bootstrap/warmer worker.

The worker is the browser half of the aggregator ingestion, kept out of the API
(which is Playwright-free by design) and deployed as its own job. It reads where
to push captured sessions and the shared bearer the API checks, plus the IMAP
mailbox it reads OTPs from.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    #: The mm-ecommerce API to push sessions to, and the bearer it checks.
    AGGREGATOR_API_URL: str = "https://api.meltingmomentscakes.com"
    AGGREGATOR_SESSION_PUSH_TOKEN: str = ""

    #: Where persisted Playwright storage states live between runs, so a warm
    #: touch resumes a logged-in context instead of logging in again. Defaults to
    #: a stable absolute path that the Dockerfile declares as a VOLUME; a deploy
    #: MUST mount a persistent volume here, or every run starts logged-out and
    #: falls into the OTP/anti-bot login path. See the README ops section.
    STORAGE_STATE_DIR: str = "/data/sessions"
    HEADLESS: bool = True
    PROBE_TIMEOUT_MS: int = 30000

    #: IMAP mailbox for OTP retrieval (Noon email OTP, Talabat where needed).
    OTP_IMAP_HOST: str = ""
    OTP_IMAP_PORT: int = 993
    OTP_IMAP_USER: str = ""
    OTP_IMAP_PASSWORD: str = Field(default="", repr=False)
    OTP_IMAP_FOLDER: str = "INBOX"

    #: Per-channel portal login credentials, used only when a stored session has
    #: gone stale and `ensure_session` has to re-establish it (mirrors the
    #: per-channel `primary_login_email` / `password` keys the standalone
    #: mm-aggregator scraper reads from its secrets YAML). Passwords are hidden
    #: from reprs so they never leak into logs or tracebacks.
    NOON_EMAIL: str = ""
    NOON_PASSWORD: str = Field(default="", repr=False)

    TALABAT_EMAIL: str = ""
    TALABAT_PASSWORD: str = Field(default="", repr=False)

    DELIVEROO_EMAIL: str = ""
    DELIVEROO_PASSWORD: str = Field(default="", repr=False)

    KEETA_EMAIL: str = ""
    KEETA_PASSWORD: str = Field(default="", repr=False)

    CAREEM_EMAIL: str = ""
    CAREEM_PASSWORD: str = Field(default="", repr=False)


settings = Settings()
