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
    model_config = SettingsConfigDict(env_file=_ENV_FILES or ".env", extra="ignore")

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

    #: Sentry, so the browser half is not a blind spot. Empty DSN ⇒ Sentry off
    #: (local/dev). The DSN is shared with the API but every event is tagged
    #: `service=aggregator-worker`, so a scraper login-fail / needs-human /
    #: reauth-exhausted / zero-capture is distinguishable from an API error.
    #: NOTE (W9): this must ALSO be named in the worker's docker-compose
    #: `environment:` allow-list or the container never sees it (it does not use
    #: the api-environment anchor).
    SENTRY_DSN: str = ""
    SENTRY_ENVIRONMENT: str = "development"

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

    # ── always-on worker daemon (Phase 3) tunables ───────────────────────────
    # Operational knobs, NOT secrets (so they are deliberately not on any secret
    # checklist): per-job hard timeouts, schedule cadences and poll intervals for
    # `daemon.py`. Defaults reproduce the retired host-cron timings.

    #: Hard per-job budgets (seconds). `asyncio.wait_for` cancels the coroutine and
    #: the daemon SIGKILLs Chrome past these — the in-process replacement for the
    #: cron's `timeout -k 30 <budget>`. RELOGIN sits just above the unattended
    #: auto-login's own 6-minute ceiling (`browser._AUTO_LOGIN_WAIT_SECONDS`) so a
    #: merely-slow login hits its graceful deadline first and the wrapper only ever
    #: catches a true wedge.
    WORKER_RELOGIN_TIMEOUT_SECONDS: int = 480
    WORKER_WARM_TIMEOUT_SECONDS: int = 600
    #: Keeta ORDERS pull (session refresh + in-page order pages) — quick, runs every
    #: few hours, so a modest budget.
    WORKER_KEETA_ORDERS_TIMEOUT_SECONDS: int = 600
    #: Keeta FINANCE pull (downloads settled statement/billing files) — the slow one,
    #: runs once nightly at lowest priority, so it gets the long budget.
    WORKER_KEETA_FINANCE_TIMEOUT_SECONDS: int = 1500
    WORKER_DELIVEROO_FINANCE_TIMEOUT_SECONDS: int = 900
    #: Keeta MENU pull (catalog sync) — two signed reads per shop, quick.
    WORKER_KEETA_MENU_TIMEOUT_SECONDS: int = 300
    #: Deliveroo MENU+HOURS pull (catalog sync) — one headed page capture through
    #: the Cloudflare wall, so it gets the same budget as the finance page.
    WORKER_DELIVEROO_MENU_TIMEOUT_SECONDS: int = 300

    #: Channels warmed nightly to rotate their decaying anti-bot cookie (Noon's
    #: Akamai bm_sv/_abck, Talabat's PerimeterX _px3). Careem/Deliveroo self-heal in
    #: the API sweep and Keeta has its own pull, so none of them belong here.
    WORKER_WARM_CHANNELS: str = "noon,talabat"
    #: Dubai wall-clock hour (0–23) for the nightly warm — kept just before the
    #: API's 23:00 (`AGGREGATOR_RUN_HOUR_DXB`) sweep so the rotated cookie is fresh
    #: when the sweep replays it. Computed in-process (Dubai is a permanent UTC+4).
    WORKER_WARM_HOUR_DXB: int = 22
    #: Dubai wall-clock hour for the nightly Deliveroo invoice (finance) pull.
    #: Moved OFF 22:00 (where it stacked on both nightly warms — three headed jobs
    #: serialised on the single consumer at once) into the deep-quiet post-midnight
    #: window. Finance downloads settled invoices, so it has no freshness constraint
    #: and can run whenever the box is idle.
    WORKER_DELIVEROO_FINANCE_HOUR_DXB: int = 2
    #: Keeta ORDERS-pull cadence (hours). Every few hours, not nightly, so each
    #: order's customer name/phone/address is captured BEFORE Keeta masks it to
    #: `***` a few hours after the order. This job is orders-only now; finance is a
    #: separate nightly job (below) so the long finance download never blocks a heal
    #: or an orders pull behind it on the single consumer.
    WORKER_KEETA_PULL_INTERVAL_HOURS: int = 3
    #: Keeta MENU-pull cadence (hours). The menu changes rarely, so a slow cadence;
    #: `<= 0` disables it. The API's catalog sync reads the pushed snapshot.
    WORKER_KEETA_MENU_INTERVAL_HOURS: int = 12
    #: Deliveroo MENU+HOURS-pull cadence (hours). `<= 0` disables it (default OFF —
    #: the catalog-sync read feature is opt-in). Set > 0 to push the snapshot the
    #: API's `_read_deliveroo_menu`/`_read_deliveroo_hours` parse.
    WORKER_DELIVEROO_MENU_INTERVAL_HOURS: int = 0
    #: Dubai wall-clock hour for the nightly Keeta FINANCE pull (settled statement
    #: files). Split from the orders pull because it re-downloads settled files and
    #: is slow; nightly + lowest priority keeps it off the hot path. Moved OFF 23:00
    #: (where the slow finance download overlapped the API's 23:00 daily ingest sweep)
    #: to a distinct post-midnight hour so the two heavy nightly workloads no longer
    #: contend for the same 2 vCPUs. Settled files have no freshness constraint.
    WORKER_KEETA_FINANCE_HOUR_DXB: int = 4
    #: How many months back the nightly Keeta finance pull lists — the LIST calls are
    #: newest-first, so this bounds how far back settled files are re-fetched (the
    #: pull used to re-download every historical file every run). 2 covers a
    #: late-settling statement while keeping the nightly download small; ingest is
    #: idempotent so a re-fetch is harmless, only wasteful.
    WORKER_KEETA_FINANCE_MONTHS_BACK: int = 2
    #: How often the daemon asks the API which sessions are dead and enqueues a
    #: RELOGIN for each (respecting the per-channel reauth backoff). Raised 120→300:
    #: the poll itself is a cheap HTTP call, but it is the CADENCE at which a
    #: marginal session gets re-driven into a headed Chrome (the steady-state CPU
    #: sink on this box). The reauth backoff + success floor already gate repeats;
    #: a slower poll lowers the re-drive rate further at negligible freshness cost —
    #: a genuinely dead session still heals within five minutes.
    WORKER_HEAL_POLL_SECONDS: int = 300
    #: Floor (seconds) on how often a channel is re-driven *after a SUCCESSFUL
    #: re-login*. The reauth backoff above only paces FAILURES; a channel whose
    #: anti-bot cookie legitimately rotates and dies again minutes after a healthy
    #: refresh (Talabat's PerimeterX `_px3`, which re-logged in 15× in one day and
    #: kept a headed Chrome pegging this e2-small's 2 vCPUs) would otherwise spawn a
    #: full headed Chrome on every heal poll it looked dead. A success stamps the
    #: channel; it is not re-driven again until this interval passes, even if it
    #: looks dead. Stamped ONLY on success, so a transient-failure retry (the short
    #: reauth backoff) is unaffected and recovery from a real blip is not slowed.
    #: Talabat's cookie expiry is advisory API-side (the token is still honoured), so
    #: a session refreshed at most this often still serves the sweep. 0 disables the
    #: floor. Tunable without a deploy.
    WORKER_MIN_RELOGIN_INTERVAL_SECONDS: int = 900
    #: How often the scheduler wakes to enqueue what is due (and beats the heartbeat
    #: the container healthcheck watches).
    WORKER_SCHEDULER_TICK_SECONDS: int = 30
    #: Random per-job spread (seconds, 0 disables) added to every scheduled fire —
    #: the daily jobs and the boot-relative interval jobs. Two purposes: it un-stacks
    #: jobs pinned to the same wall-clock hour (the two nightly warms) and the two
    #: 12h interval jobs that would otherwise re-align on every restart (the classic
    #: thundering-herd), so the single consumer meets a spread of arrivals rather
    #: than a burst. 15 min keeps the anti-bot warm comfortably inside its pre-sweep
    #: window (22:00–22:15, before the 23:00 ingest).
    WORKER_JITTER_SECONDS: int = 900
    #: Append a small set of BACKGROUND-ONLY Chrome flags (crash reporting, sync,
    #: component/background networking, first-run) to the AUTOMATED warm/pull
    #: launches to trim host CPU/RAM per browser. Every flag is host/background and
    #: none is readable by page JS, so — by the exact criterion `fingerprint.py`
    #: uses to justify `--no-sandbox` — none is an anti-bot signal. It is NOT
    #: applied to the pristine standalone `login` spawn that faces the initial
    #: Cloudflare/Akamai/PerimeterX challenge. A toggle, not a code change, so it
    #: can be switched off instantly on the VM if a channel ever regresses.
    WORKER_LEAN_CHROME: bool = True
    #: Heartbeat file the daemon touches each tick; the compose healthcheck fails
    #: the container when it goes stale. On the persisted `/data` volume.
    WORKER_HEARTBEAT_PATH: str = "/data/worker.heartbeat"


settings = Settings()
