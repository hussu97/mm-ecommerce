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
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.core.deps import get_db
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

    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT,
        integrations=[FastApiIntegration(), SqlalchemyIntegration()],
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


def make_lifespan(service: str, *, seed: bool, dispatch_batches: bool = False):
    """
    Startup checks, the i18n seed, and the batch dispatcher — each owned by
    whichever app is responsible for it.

    Only one app seeds. Two processes racing the same upsert on boot is a
    deadlock waiting to happen, and the register has no use for storefront
    copy anyway. The batch dispatcher belongs to the storefront for the same
    reason: web orders are the only ones that batch, and it holds a database
    advisory lock so running it in two places would achieve nothing but noise.
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
        if dispatch_batches and settings.BATCH_DISPATCHER_ENABLED:
            from app.services.delivery import batch_scheduler

            background.append(asyncio.create_task(batch_scheduler.run_forever()))

            # Rides with the dispatcher rather than getting a flag of its own.
            # Both are loops in the app because this stack has no cron, both
            # hold an advisory lock so a second copy would achieve nothing, and
            # both belong to whichever app already owns the shared work.
            from app.services import log_retention

            background.append(asyncio.create_task(log_retention.run_forever()))

            # Same reasoning, its own flag: this one talks to somebody else's
            # private API, so it has to be switchable off without taking the
            # dispatcher down with it. Storefront only, like its neighbours —
            # the register app must not run a second copy.
            if settings.GRUBOPS_SYNC_ENABLED:
                from app.services.grubops import grubops_reconcile

                background.append(asyncio.create_task(grubops_reconcile.run_forever()))

            # The order-ingest loop, the OOS sync's mirror image: it reads
            # aggregator orders out of the same console rather than pushing
            # availability in. Its own flag, because ingesting orders and
            # syncing stock are switched on at different times and fail
            # independently. Storefront only, like its neighbours.
            if settings.GRUBOPS_ORDERS_ENABLED:
                from app.services.grubops import grubops_orders

                background.append(asyncio.create_task(grubops_orders.run_forever()))

        logger.info("%s starting up [env=%s]", service, settings.APP_ENV)
        yield
        for task in background:
            task.cancel()
            # Awaited so a batch mid-booking finishes rather than being torn
            # off halfway between a quotation and an order.
            with suppress(asyncio.CancelledError):
                await task
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
        response.headers["X-Content-Type-Options"] = "nosniff"
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

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
        "/health", tags=["System"], summary="Health check — verifies DB connectivity"
    )
    async def health(db: AsyncSession = Depends(get_db)) -> dict:
        try:
            await db.execute(text("SELECT 1"))
            return {"status": "ok", "service": service, "env": settings.APP_ENV}
        except Exception:  # noqa: BLE001
            return JSONResponse(
                status_code=503,
                content={
                    "status": "error",
                    "service": service,
                    "env": settings.APP_ENV,
                },
            )
