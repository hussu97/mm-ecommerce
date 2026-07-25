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

import logging
from contextlib import asynccontextmanager

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
from scripts.seed_i18n import seed as seed_i18n

logger = logging.getLogger("mm.api")

MAX_BODY_BYTES = 10 * 1024 * 1024


def make_lifespan(service: str, *, seed: bool):
    """
    Startup checks, and the i18n seed for whichever app owns it.

    Only one app seeds. Two processes racing the same upsert on boot is a
    deadlock waiting to happen, and the register has no use for storefront
    copy anyway.
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
        logger.info("%s starting up [env=%s]", service, settings.APP_ENV)
        yield
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
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

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
