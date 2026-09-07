"""
Everything two FastAPI apps have to share.

The storefront/admin API and the register API are separate applications on
separate hostnames, but they are one codebase and must behave identically
where it counts: the same trusted-host and body-size limits, the same error
shape, the same request ids in the logs. Keeping that here means a security
header added once is added to both, rather than to whichever file the author
happened to have open.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from app.core import heartbeat
from app.core.background import spawn_tracked
from app.core.config import settings
from app.core.database import AsyncSessionFactory, engine, scheduler_engine
from app.core.exceptions import AppError
from app.core.limiter import limiter
from app.services import firebase_auth_service
from scripts.seed_i18n import seed as seed_i18n

logger = logging.getLogger("mm.api")

MAX_BODY_BYTES = 10 * 1024 * 1024


def configure_observability(*, service: str) -> None:
    """
    Structured logging and Sentry, for whichever app is booting.

    Both of these used to live at import time in `main.py`, so the register —
    which does not import it — ran with neither: its logs reached Cloud
    Logging as unparsed text, and `capture_exception` in the error handler
    below was a silent no-op. Every error at a till went nowhere.

    `service` is tagged on each event, because "an error in production" is not
    actionable when two applications share a database and a DSN.
    """
    _configure_logging()

    if not settings.SENTRY_DSN:
        return

    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT,
        # AsyncioIntegration so an exception escaping a background scheduler task
        # (the aggregator ingest, the batch dispatcher, …) is attributed to that
        # task rather than lost — these loops run outside any request scope.
        integrations=[
            FastApiIntegration(),
            SqlalchemyIntegration(),
            AsyncioIntegration(),
        ],
        traces_sample_rate=(
            settings.SENTRY_TRACES_SAMPLE_RATE if settings.is_production else 1.0
        ),
        send_default_pii=False,
    )
    sentry_sdk.set_tag("service", service)


def _configure_logging() -> None:
    if not settings.is_production:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        return

    from pythonjsonlogger.json import JsonFormatter

    class _GCPFormatter(JsonFormatter):
        """JSON formatter whose output GCP Cloud Logging understands out of the box.

        GCP auto-parses stdout JSON lines and uses these fields:
          - severity  → log level (maps to ERROR / WARNING / INFO etc.)
          - message   → main log text
          - time      → RFC-3339 timestamp
          - stack_trace → exception traceback (shown in Error Reporting)
          - httpRequest → structured HTTP request data
        """

        def add_fields(
            self,
            log_record: dict,
            record: logging.LogRecord,
            message_dict: dict,
        ) -> None:
            super().add_fields(log_record, record, message_dict)
            # GCP severity field (levelname is already correct: INFO/WARNING/ERROR…)
            log_record["severity"] = record.levelname
            log_record.pop("levelname", None)
            # Move exception traceback into stack_trace so Error Reporting picks it up
            if record.exc_info:
                log_record["stack_trace"] = self.formatException(record.exc_info)
                log_record.pop("exc_info", None)
                log_record.pop("exc_text", None)

    handler = logging.StreamHandler()
    handler.setFormatter(
        _GCPFormatter(
            fmt="%(asctime)s %(name)s %(message)s",
            rename_fields={"asctime": "time"},
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )

    # Apply to root logger AND uvicorn loggers so every log line is structured.
    # Without this, uvicorn writes its own plain-text lines to stderr.
    for name in ("", "uvicorn", "uvicorn.access", "uvicorn.error", "uvicorn.asgi"):
        log = logging.getLogger(name)
        log.handlers = [handler]
        log.propagate = False
    logging.root.setLevel(logging.INFO)


def make_lifespan(service: str, *, seed: bool, run_scheduler: bool = False):
    """
    Startup checks, the i18n seed, and the storefront scheduler — each owned by
    whichever app is responsible for it.

    Only one app seeds. Two processes racing the same upsert on boot is a
    deadlock waiting to happen, and the register has no use for storefront
    copy anyway. The scheduler belongs to the storefront for the same reason:
    its work — dispatch, arrivals, driver tracking, the daily email — is web
    orders' business, and each sweep holds a database advisory lock so running
    it in two places would achieve nothing but noise.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.is_production:
            if (
                settings.SECRET_KEY
                == "change-me-in-production-use-a-long-random-string-here"
            ):
                raise RuntimeError("SECRET_KEY must be changed in production")
            if not settings.STRIPE_WEBHOOK_SECRET:
                raise RuntimeError("STRIPE_WEBHOOK_SECRET must be set in production")
        if seed:
            try:
                logger.info("Running i18n seed...")
                async with AsyncSessionFactory() as session:
                    await seed_i18n(session)
            except Exception as exc:  # noqa: BLE001 — a seed must not block boot
                logger.warning("i18n seed failed (non-fatal): %s", exc)

        # Warm the courier-logo cache from the database so every order payload
        # carries the current logo. Best-effort — the module falls back to the
        # conventional URL if this cannot run — and cheap (one small query).
        try:
            from app.services.couriers import courier_catalog

            async with AsyncSessionFactory() as session:
                await courier_catalog.load(session)
        except Exception as exc:  # noqa: BLE001 — a cache warm must not block boot
            logger.warning("courier logo cache warm failed (non-fatal): %s", exc)

        background: list[asyncio.Task] = []
        if run_scheduler and settings.STOREFRONT_SCHEDULER_ENABLED:
            from app.services.delivery import delivery_scheduler

            background.append(
                spawn_tracked(
                    delivery_scheduler.run_forever(), name="delivery_scheduler"
                )
            )

            # Rides with the delivery scheduler rather than getting a flag of its
            # own. Both are loops in the app because this stack has no cron, both
            # hold an advisory lock so a second copy would achieve nothing, and
            # both belong to whichever app already owns the shared work.
            from app.services import log_retention

            background.append(
                spawn_tracked(log_retention.run_forever(), name="log_retention")
            )

            from app.services.inventory import source_event_service

            background.append(
                spawn_tracked(
                    source_event_service.run_sweeper_forever(),
                    name="inventory_source_event_sweeper",
                )
            )

            # The daily sales email. Rides here for the same reasons its
            # neighbours do — no cron in this stack, an advisory lock so a second
            # copy is harmless — and belongs to whichever app owns the shared
            # work. Sends once, after the last branch closes for the day.
            from app.services.pos import daily_sales_email

            background.append(
                spawn_tracked(daily_sales_email.run_forever(), name="daily_sales_email")
            )

            # The business-day sweeper. Same lifespan reasons as its neighbours —
            # no cron here, an advisory lock so a second copy is harmless. Hourly
            # it closes any trading day that rolled past its cut-off without an
            # end-of-day, which `close_current` alone could never reach (F-POS-26).
            from app.services.pos import business_day_service

            background.append(
                spawn_tracked(
                    business_day_service.run_forever(), name="business_day_sweeper"
                )
            )

            # Branch hours sync. Same reasoning as its neighbours — no cron here,
            # an advisory lock so a second copy is harmless, storefront app only.
            # Hourly it mirrors each branch's weekly schedule (the source of truth)
            # out to the marketplaces + Foodics, gated behind the sync flags.
            from app.services import branch_hours_sync

            background.append(
                spawn_tracked(branch_hours_sync.run_forever(), name="branch_hours_sync")
            )

            # Same reasoning, its own flag: this one talks to somebody else's
            # private API, so it has to be switchable off without taking the
            # dispatcher down with it. Storefront only, like its neighbours —
            # the register app must not run a second copy.
            if settings.GRUBOPS_SYNC_ENABLED:
                from app.services.grubops import grubops_reconcile

                background.append(
                    spawn_tracked(
                        grubops_reconcile.run_forever(), name="grubops_reconcile"
                    )
                )

            # The order-ingest loop, the OOS sync's mirror image: it reads
            # aggregator orders out of the same console rather than pushing
            # availability in. Its own flag, because ingesting orders and
            # syncing stock are switched on at different times and fail
            # independently. Storefront only, like its neighbours.
            if settings.GRUBOPS_ORDERS_ENABLED:
                from app.services.grubops import grubops_orders

                background.append(
                    spawn_tracked(grubops_orders.run_forever(), name="grubops_orders")
                )

            # The aggregator ingest: once a day at AGGREGATOR_RUN_HOUR_DXB it
            # mirrors each marketplace's ledger (sales + statements/payouts) into
            # the aggregator_* tables over httpx, replaying a browser-captured
            # session, and reconciles; plus a rolling sales-only refresh every
            # AGGREGATOR_SALES_REFRESH_MINUTES so values that settle after an order
            # is first seen (a Talabat commission landing hours later) are picked up
            # within the hour. Both schedulers are wall-clock anchored with a boot
            # catch-up, so a redeploy can never skip a run. Storefront only.
            #
            # A single LEADER-ELECTED supervisor owns both loops: `api` and
            # `api-green` both boot it, but only the slot holding the scheduler-leader
            # advisory lock actually ticks — so a blue/green cutover (two slots up at
            # once) never runs two schedulers, and a stale slot with wrong env cannot
            # keep 401ing and re-flagging sessions in the background.
            if settings.AGGREGATOR_INGEST_ENABLED:
                from app.services.aggregators import ingest as aggregator_ingest

                background.append(
                    spawn_tracked(
                        aggregator_ingest.run_aggregator_schedulers_forever(),
                        name="aggregator_ingest",
                    )
                )

        logger.info("%s starting up [env=%s]", service, settings.APP_ENV)
        yield
        for task in background:
            task.cancel()
            # Awaited so a batch mid-booking finishes rather than being torn
            # off halfway between a quotation and an order. Cap the wait:
            # an ingest tick blocked in httpx used to hold SIGTERM for the
            # full uvicorn graceful-shutdown window (~25s), which is what
            # made `docker stop -t 30` take 40s per colour on every deploy.
            with suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(task, timeout=8)
        logger.info("%s shutting down", service)

    return lifespan


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413, content={"detail": "Request body too large"}
            )
        return await call_next(request)


#: Health/liveness paths that must answer even when the request pool is full —
#: shedding these would make the container mark ITSELF unhealthy under load and
#: trigger a restart loop, turning a transient saturation into an outage.
_ADMISSION_EXEMPT = ("/ping", "/health")


class PoolSaturationMiddleware:
    """Shed load with a 503 before routing when the request pool is exhausted.

    The outermost middleware, and a raw-ASGI one on purpose: when every request
    connection is checked out, the honest answer is "come back in a moment", and
    it must be given in microseconds without touching the database, allocating a
    session, or waiting on `pool_timeout`. Piling requests up behind a full pool
    is how a brief spike (or a leaked connection) became the multi-hour 503 storms
    this package exists to prevent — a fast, cheap refusal keeps the event loop
    free to drain the in-flight work instead.

    Trips at `pool_size + max_overflow - 1`: one connection of headroom is left so
    `/health` (exempt anyway) and the last few honest requests can still be
    served while the shed protects the rest. `/ping` and `/health` are never
    shed — a healthcheck that fails under load restarts the very container that
    was coping.
    """

    def __init__(self, app, *, checkedout, threshold: int) -> None:
        self.app = app
        # A zero-arg callable returning the pool's current checked-out count,
        # injected so a test can drive saturation without a real pool.
        self._checkedout = checkedout
        self._threshold = threshold

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if not path.startswith(_ADMISSION_EXEMPT) and self._is_saturated():
            await self._reject(send)
            return
        await self.app(scope, receive, send)

    def _is_saturated(self) -> bool:
        try:
            return self._checkedout() >= self._threshold
        except Exception:  # noqa: BLE001 — a stats failure must never shed traffic
            return False

    async def _reject(self, send) -> None:
        body = b'{"detail":"Server is busy, please retry shortly"}'
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", b"2"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def configure(
    app: FastAPI, *, allowed_hosts: list[str], cors_origins: list[str]
) -> None:
    """
    Apply the shared middleware stack and error handlers.

    Order matters and is the same in both apps: first added is outermost, so
    an unknown Host is rejected before anything else runs.
    """
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        # The terminal authenticates with a device token alongside the staff
        # bearer token, so it has to survive a preflight.
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-Session-Id",
            "X-Device-Token",
        ],
    )
    app.add_middleware(MaxBodySizeMiddleware)

    from app.middleware import LoggingMiddleware, RequestIDMiddleware

    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RequestIDMiddleware)

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        # The full set, on the app rather than only in nginx's TLS server blocks.
        # They were `add_header` directives in `nginx/conf.d/ssl.conf` and
        # `pos.conf`, so the HTTP-only path (`http.conf`, used before a
        # certificate is issued) and any response that did not traverse a TLS
        # block carried none of them. Setting them here means every response from
        # either app carries them regardless of the nginx path it took.
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # HSTS only in production: it is an HTTPS-only instruction, and asserting
        # it over plain-HTTP local development would be wrong. Value matches the
        # nginx blocks (two years, subdomains, preload).
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        return response

    # Added LAST so it is the OUTERMOST middleware: a saturated server sheds the
    # request before any other work (routing, a DB session, a `pool_timeout`
    # wait) is spent on it. The threshold tracks THIS app's request pool — the
    # register (2+3) trips at 4, the storefront (5+8) at 12 — leaving one
    # connection of headroom above the shed.
    _admission_threshold = (
        settings.DATABASE_POOL_SIZE + settings.DATABASE_MAX_OVERFLOW - 1
    )
    app.add_middleware(
        PoolSaturationMiddleware,
        checkedout=engine.pool.checkedout,
        threshold=_admission_threshold,
    )

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        # `detail` is the message, unless the error carries a structured
        # `payload` — then the payload takes the slot, exactly as a dict passed
        # to `HTTPException(detail=...)` used to. Same key, same
        # string-or-object contract, so no client can tell the dialects apart.
        # See `AppError.payload`.
        body: dict[str, object] = {
            "detail": exc.payload if exc.payload is not None else exc.detail
        }
        # Only when the error carries one, so every existing response keeps the
        # exact shape it had. See `AppError.code`.
        if exc.code:
            body["code"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        sentry_sdk.capture_exception(exc)
        logger.exception("Unhandled exception: %s", exc)
        # ServerErrorMiddleware sits outside CORSMiddleware, so its responses
        # don't get CORS headers. Add them manually here.
        origin = request.headers.get("origin", "")
        headers = {}
        if origin in cors_origins:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Access-Control-Allow-Credentials"] = "true"
        return JSONResponse(
            status_code=500,
            content={"detail": "An internal server error occurred"},
            headers=headers,
        )


def add_system_endpoints(app: FastAPI, *, service: str) -> None:
    """Liveness and readiness, named so an alert says which app is down."""

    @app.get("/ping", tags=["System"], summary="Liveness probe — no dependencies")
    async def ping() -> dict:
        return {"status": "ok", "service": service}

    @app.get(
        "/health/integrations",
        tags=["System"],
        summary="Third-party reachability — for a smoke test, not a healthcheck",
    )
    async def health_integrations() -> dict:
        """
        Whether the outside services this deploy depends on answer.

        Deliberately not part of `/health`, and deliberately not what the
        container healthcheck polls (that is `/ping`, which depends on
        nothing). A probe that lets a third party mark the container unhealthy
        turns their outage into our restart loop.

        It exists because a misconfigured deploy is otherwise invisible until a
        customer meets it: phone verification with an unreachable certificate
        endpoint fails closed, quietly, and looks like nobody tried to sign up.
        """
        checks: dict[str, str] = {}
        if firebase_auth_service.is_enabled():
            reachable = await firebase_auth_service.certificates_reachable()
            checks["firebase_certificates"] = "ok" if reachable else "unreachable"
        else:
            checks["firebase_certificates"] = "disabled"
        return {"service": service, "checks": checks}

    @app.get(
        "/health",
        tags=["System"],
        summary="Health check — DB connectivity, pool stats, loop heartbeats",
    )
    async def health() -> JSONResponse:
        """Readiness with a hard budget, so it answers even under saturation.

        The old `/health` resolved `Depends(get_db)` BEFORE the handler ran, so
        under pool exhaustion it queued for the full `pool_timeout` and then
        500ed — a health probe that hangs exactly when a human most needs it to
        answer. This opens its OWN session inside an `asyncio.timeout(2)`: DB
        trouble (a full pool, a slow server) is reported as `db: "unavailable"`
        with a 503 in at most two seconds, not a hang. It also surfaces both
        pools' checkout stats and each background loop's heartbeat age, so a
        wedged sweep or a saturating pool is visible here rather than only in a
        post-mortem. `/ping` stays the trivial, dependency-free liveness probe
        the container healthcheck polls; this is for a human and for alerting.
        """
        db_ok = False
        try:
            async with asyncio.timeout(2):
                async with AsyncSessionFactory() as db:
                    await db.execute(text("SELECT 1"))
            db_ok = True
        except (Exception, TimeoutError):  # noqa: BLE001 — report, never raise
            db_ok = False

        heartbeats: dict[str, float | None] = {}
        try:
            async with asyncio.timeout(1):
                for name in heartbeat.LOOP_NAMES:
                    heartbeats[name] = await heartbeat.age_seconds(name)
        except (Exception, TimeoutError):  # noqa: BLE001 — Redis is best-effort
            heartbeats = {name: None for name in heartbeat.LOOP_NAMES}

        body = {
            "status": "ok" if db_ok else "error",
            "service": service,
            "env": settings.APP_ENV,
            "db": "ok" if db_ok else "unavailable",
            "pools": {
                "request": _pool_stats(engine),
                "scheduler": _pool_stats(scheduler_engine),
            },
            "heartbeat_ages_seconds": heartbeats,
        }
        return JSONResponse(status_code=200 if db_ok else 503, content=body)


def _pool_stats(an_engine) -> dict[str, int]:
    """checkedout/size/overflow for an async engine's pool, defensively.

    Read-only introspection; a pool implementation without these methods (a
    NullPool in a test) must not turn `/health` into a 500."""
    pool = an_engine.pool
    stats: dict[str, int] = {}
    for label, method in (
        ("checked_out", "checkedout"),
        ("checked_in", "checkedin"),
        ("size", "size"),
        ("overflow", "overflow"),
    ):
        fn = getattr(pool, method, None)
        if callable(fn):
            try:
                stats[label] = int(fn())
            except Exception:  # noqa: BLE001
                pass
    return stats
