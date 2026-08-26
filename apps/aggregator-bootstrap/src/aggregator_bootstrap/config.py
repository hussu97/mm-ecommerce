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
    #: touch resumes a logged-in context instead of logging in again.
    STORAGE_STATE_DIR: str = "secrets/sessions"
    HEADLESS: bool = True
    PROBE_TIMEOUT_MS: int = 30000

    #: IMAP mailbox for OTP retrieval (Noon email OTP, Talabat where needed).
    OTP_IMAP_HOST: str = ""
    OTP_IMAP_PORT: int = 993
    OTP_IMAP_USER: str = ""
    OTP_IMAP_PASSWORD: str = Field(default="", repr=False)
    OTP_IMAP_FOLDER: str = "INBOX"


settings = Settings()
