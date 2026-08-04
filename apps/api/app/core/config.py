from __future__ import annotations

import json
from typing import Annotated, Union

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Sentinel values that must NOT reach production
_DEV_DATABASE_URL = (
    "postgresql+asyncpg://mm_user:mm_password@localhost:5432/mm_ecommerce"
)
_DEV_SECRET_KEY = "change-me-in-production-use-a-long-random-string-here"

__all__ = [
    "Settings",
    "settings",
]


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

    # ── The register API ─────────────────────────────────────────────────────
    #: Hostnames the POS app answers to. Kept separate from ALLOWED_HOSTS so
    #: the storefront's host list and the terminal's cannot drift into each
    #: other: a bug that exposed the register on the public API host would
    #: otherwise be one careless edit away.
    #: `NoDecode` because pydantic-settings JSON-decodes a list field in the
    #: env source *before* any validator runs, so a plain
    #: "pos.example.com,localhost" would raise on boot rather than reach the
    #: parser below. The other list settings only survive because production
    #: happens to feed them JSON.
    POS_ALLOWED_HOSTS: Annotated[list[str], NoDecode] = ["*"]
    #: A native iPad app sends no Origin, so this is normally empty. It exists
    #: for a browser-based terminal or a local development console.
    POS_CORS_ORIGINS: Annotated[list[str], NoDecode] = []
    #: Once the register has its own hostname, refuse device tokens anywhere
    #: else. Off by default because turning it on before pos.* resolves would
    #: strand every terminal with nowhere to authenticate — the cutover has to
    #: be a deliberate flip after DNS is live, not a side effect of a deploy.
    POS_REQUIRE_POS_HOST: bool = False

    @field_validator("POS_ALLOWED_HOSTS", "POS_CORS_ORIGINS", mode="before")
    @classmethod
    def parse_pos_lists(cls, v: Union[str, list]) -> list[str]:
        if isinstance(v, str):
            if not v.strip():
                return []
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [item.strip() for item in v.split(",") if item.strip()]
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

    # ── Lalamove (courier) ───────────────────────────────────────────────────
    #: Leave the key and secret empty to run without a courier: zones marked
    #: `lalamove` then behave exactly like third-party ones — priced the same,
    #: dispatched by hand — so a missing credential degrades to today's flow
    #: instead of stalling orders.
    LALAMOVE_API_KEY: str = ""
    LALAMOVE_API_SECRET: str = ""
    #: "production" or "sandbox". Sandbox has no working AE pricing engine and
    #: an unfunded wallet, so it is only useful against another market.
    LALAMOVE_ENV: str = "production"
    LALAMOVE_MARKET: str = "AE"
    #: Validated by Lalamove to be exactly this for the UAE.
    LALAMOVE_LANGUAGE: str = "en_AE"
    #: CAR is the smallest vehicle offered in the UAE (0.5 m³, 80 kg).
    LALAMOVE_SERVICE_TYPE: str = "CAR"
    #: Comma-separated `specialRequests`. Empty by design: `DOOR_TO_DOOR` was
    #: sent until Aug 2026 at a flat +5 AED per order, and the AED 5 buys a
    #: promise the driver already keeps in practice. Dropping it takes 5 AED off
    #: every Lalamove booking. Set it back here if that turns out to be wrong.
    LALAMOVE_SPECIAL_REQUESTS: str = ""
    #: Signing covers the path only, and it must match the path Lalamove is
    #: configured to POST to, byte for byte, or every webhook fails validation.
    LALAMOVE_WEBHOOK_PATH: str = "/api/v1/webhooks/lalamove"
    LALAMOVE_TIMEOUT_SECONDS: float = 8.0
    #: Checkout re-quotes on every pin move, so identical points inside this
    #: window reuse the last answer rather than spending the 100/min budget.
    LALAMOVE_QUOTE_CACHE_SECONDS: int = 120
    #: Which branch the courier collects from. Falls back to the first active
    #: branch that takes online orders and has coordinates.
    LALAMOVE_PICKUP_BRANCH_REF: str = ""
    #: Who the driver calls at the shop. Falls back to the branch's own phone.
    LALAMOVE_SENDER_PHONE: str = ""
    #: The in-process loop that sends a batch when its window closes. There is
    #: no queue in this stack, so this is the only thing that fires them.
    #: Turning it off leaves batches sitting until someone dispatches them by
    #: hand from the admin — useful for a maintenance window, dangerous as a
    #: default, which is why it is on.
    BATCH_DISPATCHER_ENABLED: bool = True

    # ── noon Send / Rider-on-Demand (courier) ────────────────────────────────
    #: Same contract as Lalamove above: an empty key means a `noon_send` zone
    #: prices and sells exactly as it does today and simply dispatches through
    #: Lalamove instead, so a missing credential is a fallback, not an outage.
    NOON_SEND_API_KEY: str = ""
    #: "production" or "staging", and **this deliberately stays `staging` on the
    #: production deployment** while the trial runs. We have no production noon
    #: Send key yet, and their staging environment is real enough to exercise
    #: the whole pipeline — the kitchen's own coordinates come back serviceable
    #: there, tasks are created, tracked and cancelled for real.
    #:
    #: The consequence is the important part: a task created against staging is
    #: **not dispatched to a real rider**. Only the trial accounts in
    #: `TRIAL_CUSTOMER_EMAILS` are routed here, and somebody has to carry their
    #: orders by hand. This is why those accounts also get free delivery.
    #:
    #: Nothing about the customer gate depends on this value. Which customers
    #: noon Send may carry is `TRIAL_CUSTOMER_EMAILS` and nothing else, so
    #: pointing this at production later does not quietly widen the trial.
    NOON_SEND_ENV: str = "staging"
    #: Sent on every call. `en-ae` or `en-sa`; only the UAE fleet concerns us.
    NOON_SEND_LOCALE: str = "en-ae"
    #: Fallback pickup point, for a branch with no code of its own.
    #:
    #: The real home for this is `branches.noon_send_outlet_code`, set in the
    #: admin — a courier collects from a *place*, and one environment variable
    #: cannot hold two answers once a second kitchen starts delivering. This
    #: remains so the current single-kitchen deployment keeps working without
    #: anyone touching the admin, and so a fresh environment has something to
    #: point at before any branch is registered.
    NOON_SEND_OUTLET_CODE: str = ""
    #: `noon_food` or `nownow` — which side of noon owns the pickup point.
    NOON_SEND_CLIENT_CODE: str = "noon_food"
    #: Their hard cap on pickup-to-drop-off distance, and the belt to the zone
    #: map's braces: a task that would be rejected is never sent.
    #:
    #: Must not be tighter than the circle `Sharjah Central` is drawn to, or the
    #: outer ring of that zone is refused by us before noon Send ever sees it and
    #: falls back to Lalamove — invisibly, and on exactly the addresses the zone
    #: exists to serve. It was 15000 against a 20 km zone for one release, which
    #: silently excluded Al Zahia and University City. `test_noon_send_service`
    #: now ties the two together. `GET /public/v1/configurations` reports the
    #: real per-partner limit if commercial ever raise ours.
    NOON_SEND_MAX_DISTANCE_M: int = 20000
    #: Straight-line to road-distance multiplier, fitted across the sixteen
    #: Sharjah areas the Lalamove rate card was measured over. Only used to
    #: estimate what a run costs us — noon Send has no quotation API, so this is
    #: the only cost figure that will ever exist for one of their tasks.
    NOON_SEND_DETOUR_FACTOR: float = 1.49
    #: The published rate card, which has a vehicle tier. AED 12 on a bike is
    #: what makes this courier worth having — on a bike they beat Lalamove at
    #: every distance in range; in the bulky car product at AED 25 they lose at
    #: every distance in range. Standard cakes go by bike.
    NOON_SEND_BASE: float = 12.0
    NOON_SEND_BULKY_BASE: float = 25.0
    #: Added across all bands during 12:00–15:00 and 19:00–22:00 Dubai time.
    NOON_SEND_SURGE_AED: float = 1.0
    #: The key noon Send presents on the status and tracking webhooks. They have
    #: no request signing, so this shared secret is the only thing separating a
    #: real status update from anyone who guesses the URL.
    NOON_SEND_WEBHOOK_API_KEY: str = ""
    NOON_SEND_TIMEOUT_SECONDS: float = 8.0

    # ── Apple Push (the POS registers) ────────────────────────────────────────
    #: The APNs auth key, as `.p8` PEM. Team-scoped and account-wide: one key
    #: serves every app in the Apple team and both the sandbox and production
    #: hosts, and Apple caps an account at two — so this is the same key the
    #: other apps use, not a new one.
    #:
    #: Written by the deploy workflow as a single line with literal `\n`,
    #: because a multi-line GitHub secret does not survive a `printf` into
    #: `.env`. The provider puts the newlines back.
    #:
    #: Empty means no push at all: orders still arrive and the register still
    #: shows them when it polls, so a missing key is a quieter shop, not a
    #: broken one.
    APNS_KEY_P8: str = ""
    #: The ten-character Key ID shown next to the key in the Apple portal.
    APNS_KEY_ID: str = ""
    #: The Apple Team ID. This is the `OU` of a signing certificate, not the
    #: ten characters in a certificate's common name.
    APNS_TEAM_ID: str = ""
    #: Sound file bundled in the app, played for a new order. The repeating
    #: alarm is the app's own doing — iOS will not loop a notification sound.
    APNS_ORDER_SOUND: str = "new-order.caf"
    APNS_TIMEOUT_SECONDS: float = 10.0

    # ── Trial customers ───────────────────────────────────────────────────────
    #: The accounts running live tests against production. Comma-separated, and
    #: matched against a **signed-in** customer's own address — a guest checkout
    #: never qualifies, because an email is a string anybody may type.
    #:
    #: Two things follow from being on this list, and they are two halves of one
    #: decision: noon Send carries the order, and delivery is free. See
    #: `app/services/trial_customer.py`. Empty ends the trial — noon Send opens
    #: to every customer in its zone and nobody gets free delivery.
    #:
    #: This list is the *only* thing gating noon Send. It applies in every
    #: environment, so it cannot be widened by a deployment setting.
    TRIAL_CUSTOMER_EMAILS: str = "h_abbasi97@hotmail.com"

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
    SENTRY_TRACES_SAMPLE_RATE: float = 0.02

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

    @property
    def cookie_secure(self) -> bool:
        """Cookies must be Secure in production (HTTPS-only)."""
        return self.is_production

    @property
    def cookie_samesite(self) -> str:
        """Use 'lax' for same-site browsing; works for cross-port localhost and same-domain prod."""
        return "lax"


settings = Settings()
