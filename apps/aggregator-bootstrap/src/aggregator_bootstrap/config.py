"""Settings for the bootstrap/warmer worker.

The worker is the browser half of the aggregator ingestion, kept out of the API
(which is Playwright-free by design) and deployed as its own job. It reads where
to push/pull captured sessions and the shared bearer the API checks.

A headed `login` mints a session; `warm-sessions` hydrates it from the API on
every start (so a deploy with an empty volume still resumes) and rotates
anti-bot cookies. IMAP OTP is not part of that path.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Load `apps/api/.env` as well as this package's `.env`. The token lives in
#: the API file on a laptop (the same place the rest of local secrets sit);
#: a worker-only `.env` still wins when both exist, and real env vars win
#: over both.
_PKG_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILES = tuple(
    str(path)
    for path in (
        _PKG_ROOT.parent / "api" / ".env",
        _PKG_ROOT / ".env",
    )
    if path.is_file()
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES or ".env", extra="ignore"
    )

    #: The mm-ecommerce API to push sessions to, and the bearer it checks.
    AGGREGATOR_API_URL: str = "https://api.meltingmomentscakes.com"
    AGGREGATOR_SESSION_PUSH_TOKEN: str = ""

    #: Where persisted Playwright storage states live between runs, so a warm
    #: touch resumes a logged-in context instead of logging in again. Defaults to
    #: a stable absolute path that the Dockerfile declares as a VOLUME. This is
    #: a *cache*: the API's `aggregator_session` row is the source of truth, and
    #: `hydrate` rewrites these files from it on every start. A persistent
    #: volume still helps the worker survive an API blip.
    STORAGE_STATE_DIR: str = "/data/sessions"
    HEADLESS: bool = True
    PROBE_TIMEOUT_MS: int = 30000

    #: IMAP mailbox — unused on the default path. Kept so a last-resort OTP
    #: helper still has somewhere to read from if an operator opts in.
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
