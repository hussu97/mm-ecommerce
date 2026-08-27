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

    # ── Stripe (card gateway) ─────────────────────────────────────────────────
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""

    # ── Ziina (card gateway) ─────────────────────────────────────────────────
    #
    # The second processor for card payments. Which of the two a given order
    # goes through is decided at runtime from the `payment_gateways` table, not
    # from here — see `payment_gateway_router`. These are only the credentials.
    #
    # **Production runs on Stripe.** Ziina exists so that a Stripe incident is a
    # row update rather than a deploy, and until it has been signed off it must
    # not take live traffic. Three independent things have to be true before it
    # can: the `payment_gateways` row is active (it ships inactive),
    # `ZIINA_ENABLED` is true (it defaults false, everywhere), and a key is
    # present. Any one of them false and the router will not pick it.
    #
    #: The master switch. Deliberately separate from the API key: keys turn up
    #: on a VM for all sorts of reasons and none of them is a decision to start
    #: charging real cards through a new processor.
    ZIINA_ENABLED: bool = False
    ZIINA_API_KEY: str = ""
    #: The secret we registered with `POST /webhook`. Ziina signs the raw body
    #: with it as a hex SHA-256 HMAC in `X-Hmac-Signature`. Without it the
    #: webhook endpoint refuses every push rather than trusting an unsigned one.
    ZIINA_WEBHOOK_SECRET: str = ""
    ZIINA_API_URL: str = "https://api-v2.ziina.com/api"
    #: Sends `test: true` on every payment intent, which takes test cards and
    #: charges nothing. The `payment_gateways` row can also ask for this
    #: per-gateway; either one turning it on is enough.
    ZIINA_TEST_MODE: bool = False
    ZIINA_TIMEOUT_SECONDS: int = 10

    # ── Resend (email) ────────────────────────────────────────────────────────
    RESEND_API_KEY: str = ""
    FROM_EMAIL: str = "noreply@meltingmomentscakes.com"

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
    #: Cloudflare Turnstile, on signup and password reset. Empty disables the
    #: check entirely — see `turnstile_service` for why that is the deliberate
    #: default rather than a locked door.
    # ── Firebase phone verification ───────────────────────────────────────
    #: The Firebase/GCP project whose ID tokens we accept. **Not a secret** —
    #: it is in the browser bundle already — but it is the entire audience
    #: check, so a wrong value accepts tokens minted by somebody else's project.
    #: Empty disables phone verification rather than opening it.
    FIREBASE_PROJECT_ID: str = ""
    #: How long a phone proof stays reusable, in seconds. Firebase ID tokens
    #: refresh hourly and carry `auth_time` from the original sign-in, so
    #: without this a months-old session still presents as freshly verified.
    #: Zero disables the age check.
    FIREBASE_MAX_AUTH_AGE_SECONDS: int = 3600
    FIREBASE_TIMEOUT_SECONDS: float = 5.0
    #: How long a completed phone proof stays good for, in seconds. Separate
    #: from the token's own freshness: that is about replay, this is about not
    #: making a customer re-verify between saving an address and paying.
    #: 30 days by default.
    PHONE_VERIFICATION_TTL_SECONDS: int = 2592000

    TURNSTILE_SECRET_KEY: str = ""
    TURNSTILE_TIMEOUT_SECONDS: float = 5.0

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
    #: "production" or "staging". Which fleet a task is created against, and so
    #: whether a real rider is dispatched at all: a staging task is created,
    #: tracked and cancelled for real, and collected by nobody.
    #:
    #: This defaulted to `staging` while the integration was proved, with a
    #: named allow-list deciding who was routed there. Both are gone — every
    #: order in a `noon_send` zone goes to noon Send, so the value here is now
    #: the only thing standing between a customer's cake and a real rider.
    #: `NOON_SEND_API_KEY` must be the matching key for whichever it names.
    NOON_SEND_ENV: str = "production"
    #: Whether an incoming noon Send webhook must present the key we configured.
    #:
    #: Off, because turning it on blind is the failure it is meant to prevent.
    #: noon Send does not sign requests and their staging side sends a key no
    #: screen of ours produced, so enforcing a match once dropped every status
    #: update for the trial — `assigned` and `picked_up` both arrived and both
    #: were discarded. Until the fingerprint they send matches the fingerprint
    #: we hold (both are recorded on every `webhook_logs` row for exactly this
    #: comparison), refusing on a mismatch loses live deliveries.
    #:
    #: Compare the two in `webhook_logs`, then set this to `true`.
    NOON_SEND_ENFORCE_WEBHOOK_KEY: bool = False
    #: Sent on every call. `en-ae` or `en-sa`; only the UAE fleet concerns us.
    NOON_SEND_LOCALE: str = "en-ae"
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

    # ── routing ───────────────────────────────────────────────────────────────
    #
    #: Mapbox Directions, for "how far is the driver from the kitchen" — the one
    #: question the counter asks that neither courier answers.
    #:
    #: Optional by design. Without it `driver_proximity` falls back to the
    #: straight-line estimate above, which is the behaviour that shipped first
    #: and is still correct to within a detour factor. A missing token must cost
    #: precision, never a blank screen or a failed webhook.
    #:
    #: A `pk.` public token: the Directions API needs no scope, and any secret
    #: scope would make this an `sk.` token with a much larger blast radius.
    #: Deliberately **not** URL-restricted — Mapbox enforces those on the HTTP
    #: `Referer` header, which a server request does not send, so a restricted
    #: token would reject every call we make with an error that reads like a bad
    #: key. It is a server secret and never reaches a browser.
    MAPBOX_ACCESS_TOKEN: str = ""
    #: Seconds between route refreshes for one delivery.
    #:
    #: noon Send pushes a position every 15-30 seconds and the sweep ticks every
    #: minute, so without a floor a single order could ask Mapbox four times a
    #: minute to re-answer a question whose answer has not changed. A driver does
    #: not cross a meaningful amount of Sharjah in sixty seconds.
    MAPBOX_MIN_ROUTE_INTERVAL_S: int = 60
    #: How long to wait on Mapbox before giving up and using the estimate.
    #:
    #: Short: this runs inside the batch sweep, which has cakes waiting on it.
    MAPBOX_TIMEOUT_S: float = 5.0
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

    # ── GrubOps (GrubTech aggregator console) ─────────────────────────────────
    #: Mirrors "mark out of stock" from the terminal onto the aggregators, so
    #: that a counter that has run out of pistachio says so once instead of
    #: twice. One way only: this app is the source of truth and GrubOps is told.
    #:
    #: Off by default and meant to stay off until the item map has been seeded
    #: and approved — a sync with a half-built map would take the wrong things
    #: off Talabat. See `services/grubops_reconcile.py`.
    GRUBOPS_SYNC_ENABLED: bool = False
    #: GrubTech publish no partner API, so the integration signs in as a console
    #: user against their Cognito pool. A real login, with a real password, and
    #: the reason the whole feature sits behind one provider and one flag.
    GRUBOPS_USERNAME: str = ""
    GRUBOPS_PASSWORD: str = ""
    #: The account's own identifiers. Defaulted to this shop's rather than left
    #: blank because they are not secret, they are stable, and a missing one is
    #: a silent no-op that takes an afternoon to find.
    GRUBOPS_PARTNER_ID: str = "6922fe267f5b1c6d208c634f"
    #: The GrubOps 2.0 **console** app client (pool eu-west-2_Lp8Eb8HmS) — not the
    #: integration client `2d8lmtmc…` (pool eu-west-2_CKectL0Mu) we used first.
    #: Same user, same USER_PASSWORD_AUTH, same `permissionValues: ALL` — but
    #: `order-management/order-force-complete` authorises on the token's `aud` and
    #: 403s ("Force completed unsuccessful") every client but the console's. The
    #: console client is a superset — verified serving every read the sync makes
    #: (orders, locations, catalogue, availability) — so the whole sync uses it.
    #: Do not "tidy" this back to the integration client; see
    #: docs/grubops-integration.md.
    GRUBOPS_COGNITO_CLIENT_ID: str = "75n3em3l16kvhnf6c512680vm9"
    GRUBOPS_COGNITO_REGION: str = "eu-west-2"
    GRUBOPS_API_BASE: str = "https://internal-api.grubtech.io"
    #: The catalogue lives on a different host from the availability service.
    #: `serving-brands` and the menu listing answer here; the availability
    #: writes answer on `GRUBOPS_API_BASE`. Two bases rather than one because
    #: GrubTech split them, not because we wanted the choice.
    GRUBOPS_CATALOG_API_BASE: str = "https://api-grubone.grubtech.io"
    #: Stamped on every availability record we write, and deliberately the same
    #: string their own console stamps — it is the GrubOps app name, and every
    #: record on the account already carries it.
    #:
    #: The alternative was a marker of our own, which would have made our writes
    #: identifiable. It also would have made them *different*: a source nothing
    #: else in their system produces, on a private API with no contract, sitting
    #: in a field their UI renders. Blending in is the smaller risk, and nothing
    #: here needed the marker — the reconcile loop takes desired state from our
    #: database and never from theirs, so it cannot mistake their writes for
    #: ours whatever they are labelled.
    GRUBOPS_SOURCE: str = "grubOps 2.0"
    GRUBOPS_TIMEOUT_SECONDS: float = 8.0
    #: How often the reconcile loop recomputes. Availability changes are
    #: human-paced, and a shorter tick would only spend somebody else's quota.
    GRUBOPS_RECONCILE_TICK_SECONDS: int = 120

    # ── GrubOps orders (aggregator order ingest) ──────────────────────────────
    #: The other direction from the OOS sync: pull Talabat/Noon/Careem/Deliveroo/
    #: Keeta orders out of GrubOps and land them in `orders` as `source=aggregator`.
    #: Off until the ingest has been watched once in production — a wrong branch
    #: map or a bad status mapping writes into the book of record. See
    #: `services/grubops_orders.py`. Reuses the OOS credentials and token cache.
    GRUBOPS_ORDERS_ENABLED: bool = False
    #: Orders answer on the console host, which is a third GrubTech host distinct
    #: from the availability (`GRUBOPS_API_BASE`) and catalogue
    #: (`GRUBOPS_CATALOG_API_BASE`) ones. Their split, not ours.
    GRUBOPS_ORDERS_API_BASE: str = "https://api-grubops.grubtech.io"
    #: How often the ingest loop polls. Orders are time-sensitive to a customer
    #: waiting on a cake, so this is far shorter than the availability tick; the
    #: cheap `getOrderCount` probe keeps an unchanged tick close to free.
    GRUBOPS_ORDERS_TICK_SECONDS: int = 60
    #: How long an aggregator order sits at `packed` before the ingest loop closes
    #: it to `delivered` itself. The aggregator gives us no on-the-way or delivered
    #: signal once its rider is called (Foodics dispatch), so from our side the order
    #: is done a few minutes later; this is that few minutes. Resolved at the poll
    #: tick, so the effective delay is this rounded up to the next tick. The same
    #: `packed -> delivered` move also fires the Foodics **Close** (see below).
    AGG_AUTO_CLOSE_SECONDS: int = 300

    # ── Foodics (aggregator order write-back, via the console) ────────────────
    #: MM drives the aggregator order forward through **Foodics** — the POS behind
    #: GrubTech — rather than through GrubOps's blunt force-* overrides. When the
    #: shop presses Packed we *dispatch* the Foodics order (its "ready to deliver",
    #: which GrubTech cascades to the aggregator rider via the normal flow,
    #: verified 2026-08-25 on Noon order 4961); five minutes later the auto-close
    #: move *finalises* it (delivery_status=delivered); a cancel *declines* it.
    #: Reads still come from the GrubOps ingest loop, which is where we learn each
    #: order's Foodics id.
    #:
    #: **This is the console integration, not the Foodics developer/OAuth API.** We
    #: sign in as the business the way `console.foodics.com` does and call its
    #: private `core-api` — the same approach as the GrubOps provider. Auth is a
    #: session cookie + CSRF token the provider obtains at runtime by logging in
    #: with the account number + email + password below, presenting as Chrome in
    #: the UAE the way a person at the console does.
    #:
    #: Off by default: a bad login or status mapping would drive real orders.
    #: Independent of `GRUBOPS_ORDERS_ENABLED` (ingest) so the read side can run
    #: without the write side.
    FOODICS_ORDER_PUSH_ENABLED: bool = False
    FOODICS_CONSOLE_BASE: str = "https://console.foodics.com"
    #: The account (business reference) + owner login the console signs in with.
    #: Not secret in themselves; the password is.
    FOODICS_ACCOUNT_NUMBER: str = "862261"
    FOODICS_EMAIL: str = ""
    FOODICS_PASSWORD: str = ""
    FOODICS_TIMEOUT_SECONDS: float = 8.0

    # ── Aggregator ingestion (Careem/Deliveroo/Talabat/Noon/Keeta) ────────────
    #: The hourly sales sweep and daily finance/reconciliation sweep that mirror
    #: each marketplace's own ledger into `aggregator_*` and reconcile it against
    #: MM orders. Off until a session has been bootstrapped and the ingest
    #: watched once — a wrong outlet map writes nonsense into the reconciliation.
    #: Storefront only, like the GrubOps loops; the register never runs it.
    AGGREGATOR_INGEST_ENABLED: bool = False
    #: How many days back order *promotion* turns scraped orders into real MM
    #: orders (with product mapping and stock). Separate from the ledger sweep
    #: window (`AGGREGATOR_LOOKBACK_DAYS`): the sweep mirrors a wide window cheaply,
    #: but promotion writes real stock-affecting orders, so it is kept narrow —
    #: last day only for now — and widened as we scale. Guards against a first run
    #: backfilling months of history and double-decrementing stock for sales that
    #: already happened.
    AGGREGATOR_PROMOTE_LOOKBACK_DAYS: int = 1
    #: Fernet key that encrypts the derived session blobs (cookies, tokens, the
    #: captured header fingerprint) at rest in `aggregator_session`. Empty means
    #: no session can be stored or read — the ingest stays inert rather than
    #: holding a marketplace's credentials in plaintext. Generate with
    #: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
    AGGREGATOR_CONFIG_ENCRYPTION_KEY: str = ""
    #: Bearer the bootstrap/warmer worker presents to `POST /aggregators/session`
    #: when it pushes a freshly captured session in. The one write path into
    #: `aggregator_session`, so an empty token closes it.
    AGGREGATOR_SESSION_PUSH_TOKEN: str = ""
    #: The hour (Asia/Dubai, 0–23) of the once-daily pass that mirrors sales +
    #: finance and runs reconciliation/promotion for every aggregator in one go.
    #: Wall-clock anchored so a redeploy cannot shift it; a slot missed while the
    #: process was down is caught up on next boot. 23:00 keeps the pull after the
    #: trading day has closed and off the marketplaces' busy hours.
    AGGREGATOR_RUN_HOUR_DXB: int = 23
    #: How many days back each daily pass re-pulls — one window for both sales and
    #: finance. Orders mutate after creation and statements post days late, so the
    #: run re-pulls a rolling multi-day window and upserts idempotently (the
    #: overlap is free). Sized to swallow a multi-day outage: even after several
    #: missed days, the next run backfills everything in range.
    AGGREGATOR_LOOKBACK_DAYS: int = 10
    AGGREGATOR_TIMEOUT_SECONDS: float = 20.0
    #: Ceiling on outbound calls to each marketplace. PerimeterX/Akamai score
    #: bursts; the sales sweep is hourly so 1 req/s is ample. 0 disables it.
    AGGREGATOR_REQUESTS_PER_SECOND: float = 1.0

    # ── Slider (courier) ──────────────────────────────────────────────────────
    #: The third courier, and the same contract as the other two: an empty key
    #: means a `slider` zone prices and sells exactly as it does today and
    #: dispatches through somebody else, so a missing credential is a fallback
    #: rather than an outage.
    #:
    #: Slider is here for dispatch accuracy rather than for price. Across the
    #: Ajman, Dubai and Umm al-Quwain zones its car costs about AED 0.94 an
    #: order more than Lalamove; Ajman alone is cheaper on every measured area,
    #: because Lalamove's 17 AED base is high and Ajman is close.
    #:
    #: Sent as `X-Slider-Key`. A Bearer token is ignored by their API. Swap it
    #: and `SLIDER_ACCOUNT_ID` together — the production account is not the
    #: sandbox one they were proved against.
    SLIDER_API_KEY: str = ""
    #: The account the deliveries are booked against. Sent in the request
    #: **body** as `account_id` — an `X-Account-Id` header is ignored.
    SLIDER_ACCOUNT_ID: str = ""
    #: The one host is `slider_provider.BASE_URL`
    #: (`https://api.slider-app.com/v1`), confirmed live on 2026-08-21. The
    #: sandbox and the `SLIDER_ENV` override were removed when the pilot moved to
    #: production; a moved hostname is now a code change and a deploy.
    SLIDER_TIMEOUT_SECONDS: float = 8.0
    #: The vehicle rule, and the whole of it: **a bike may not cross an emirate
    #: boundary**, so from the Sharjah kitchen the bike tier is usable inside
    #: Sharjah and only there. Both conditions, not just distance — same emirate
    #: as the pickup *and* no more than this many **road** kilometres.
    #:
    #: ⚠️ This contradicts what Slider's API says. `/deliveries/fare` returned
    #: `bike: is_available: true` for Sharjah→Ajman at 12 km and for nine
    #: Sharjah→Dubai routes out to 34.4 km. Either they do allow cross-emirate
    #: bikes, or their API is offering a vehicle they cannot dispatch. Until
    #: somebody at Slider answers that in writing, we assume the stricter
    #: reading — it is worth roughly AED 5 an order across Ajman and Dubai, and
    #: the wrong guess in this direction costs money while the wrong guess in
    #: the other strands a cake.
    #:
    #: Road kilometres. Never compare this against a straight-line figure: the
    #: fare survey measured the real ratio at 1.44 mean, so 35 road km is about
    #: 24 km crow-flies.
    SLIDER_BIKE_MAX_KM: float = 35.0
    #: Straight-line to road-distance multiplier, measured across the 97 areas
    #: of the Slider fare survey: 1.44 mean, 1.24 median. Used only when Slider
    #: has not told us a distance themselves — their fare response carries the
    #: road distance they bill against, and that is always preferred.
    SLIDER_DETOUR_FACTOR: float = 1.44
    #: The static token Slider presents on the production webhook, and the
    #: header they present it in. They do not sign requests, so this pair is the
    #: entire check — which is why it is enforced here rather than merely
    #: recorded. (Contrast `NOON_SEND_ENFORCE_WEBHOOK_KEY`, which is false in
    #: production and leaves that endpoint effectively open.)
    #:
    #: Both halves matter. A token with no header name may not be sent at all,
    #: or may arrive in a header nobody is reading.
    SLIDER_WEBHOOK_TOKEN: str = ""
    SLIDER_WEBHOOK_HEADER: str = "X-Slider-Token"
    #: The accounts running the Slider pilot on production. Comma-separated, and
    #: matched against a **signed-in** customer's own address — a guest checkout
    #: never qualifies, because an email is a string anybody may type.
    #:
    #: Two things follow from being on this list and they are two halves of one
    #: decision: Slider carries the order, and delivery is free. See
    #: `app/services/trial_customer.py`. Emptying it ends the pilot — Slider
    #: opens to its zones and nobody gets free delivery, both at once.
    #:
    #: This list is the *only* thing gating Slider. It applies in every
    #: environment, deliberately: an environment-shaped gate opens a trial to
    #: everybody the moment the environment changes.
    SLIDER_TRIAL_EMAILS: str = ""

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

    # ── Frontend URLs (email templates & CORS) ────────────────────────────────
    WEB_URL: str = "http://localhost:3000"
    ADMIN_URL: str = "http://localhost:3001"

    # ── Log retention ─────────────────────────────────────────────────────────
    #: How long `webhook_logs`, `email_logs` and `webhook_events` are kept.
    #:
    #: These are debugging output, and the question they answer — "did this
    #: arrive, did that send" — is asked within hours. `webhook_logs` is also
    #: the fastest-growing table in the database by an order of magnitude: noon
    #: Send push a rider position every 15-30 seconds per live task, and every
    #: one is stored at full payload. The bound is what makes that affordable.
    #: Swept hourly by `app/services/log_retention.py`.
    LOG_RETENTION_DAYS: int = 7
    #: How long `audit_logs` is kept, and deliberately far longer.
    #:
    #: Different in kind from the three above: not debugging output but the
    #: record of who changed what, wanted precisely when somebody disputes a
    #: change weeks after it happened. Seven days would mean a question raised a
    #: fortnight later has no answer.
    AUDIT_RETENTION_DAYS: int = 90

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

        # Turning Ziina on in production is a deliberate act, and a half-done
        # one is worse than not doing it: a gateway with a key and no webhook
        # secret takes real money and then refuses every event telling us it
        # did, leaving paid orders sitting in `created`. Fail on boot instead,
        # while the previous container is still serving.
        if self.ZIINA_ENABLED:
            for name in ("ZIINA_API_KEY", "ZIINA_WEBHOOK_SECRET"):
                if not getattr(self, name):
                    errors.append(
                        f"ZIINA_ENABLED is set but {name} is empty — a gateway "
                        "that can charge and cannot be told what it charged"
                    )

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
