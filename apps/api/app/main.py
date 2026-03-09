from __future__ import annotations

import logging
import logging.config
from contextlib import asynccontextmanager

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

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.core.deps import get_db
from app.core.exceptions import AppError
from app.core.limiter import limiter
from scripts.seed_i18n import seed as seed_i18n

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

if settings.is_production:
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

    _handler = logging.StreamHandler()
    _handler.setFormatter(
        _GCPFormatter(
            fmt="%(asctime)s %(name)s %(message)s",
            rename_fields={"asctime": "time"},
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )

    # Apply to root logger AND uvicorn loggers so every log line is structured.
    # Without this, uvicorn writes its own plain-text lines to stderr.
    for _logger_name in (
        "",
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
        "uvicorn.asgi",
    ):
        _log = logging.getLogger(_logger_name)
        _log.handlers = [_handler]
        _log.propagate = False
    logging.root.setLevel(logging.INFO)
else:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
logger = logging.getLogger("mm.api")

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


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
    try:
        logger.info("Running i18n seed...")
        async with AsyncSessionFactory() as session:
            await seed_i18n(session)
    except Exception as exc:
        logger.warning("i18n seed failed (non-fatal): %s", exc)
    logger.info("Melting Moments API starting up [env=%s]", settings.APP_ENV)
    yield
    logger.info("Melting Moments API shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

_docs_url = None if settings.is_production else "/docs"
_redoc_url = None if settings.is_production else "/redoc"
_openapi_url = None if settings.is_production else "/openapi.json"

app = FastAPI(
    title="Melting Moments API",
    description="Backend API for Melting Moments Cakes — UAE artisanal bakery",
    version="0.1.0",
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    openapi_url=_openapi_url,
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# Middleware (first added = outermost — processes requests first)
# ---------------------------------------------------------------------------

# 1. Trusted host — outermost, reject unknown Host headers early
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)

# 2. GZip — compress responses >= 1KB
app.add_middleware(GZipMiddleware, minimum_size=1024)

# 3. CORS — restrict to specific methods and headers
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Session-Id"],
)


# 4. Request size limit (10 MB)
class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 10 * 1024 * 1024:
            return JSONResponse(
                status_code=413, content={"detail": "Request body too large"}
            )
        return await call_next(request)


app.add_middleware(MaxBodySizeMiddleware)

# 5. Request ID + Logging (innermost of the add_middleware stack)
from app.middleware import LoggingMiddleware, RequestIDMiddleware  # noqa: E402

app.add_middleware(LoggingMiddleware)
app.add_middleware(RequestIDMiddleware)

# ---------------------------------------------------------------------------
# Security headers (applied after response is built)
# ---------------------------------------------------------------------------


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception: %s", exc)
    # ServerErrorMiddleware sits outside CORSMiddleware, so its responses
    # don't get CORS headers. Add them manually here.
    origin = request.headers.get("origin", "")
    headers = {}
    if origin in settings.CORS_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred"},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(api_router, prefix="/api/v1")

# ---------------------------------------------------------------------------
# System endpoints
# ---------------------------------------------------------------------------


@app.get("/ping", tags=["System"], summary="Liveness probe — no dependencies")
async def ping() -> dict:
    return {"status": "ok"}


@app.get("/health", tags=["System"], summary="Health check — verifies DB connectivity")
async def health(db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ok", "service": "mm-api", "env": settings.APP_ENV}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "service": "mm-api", "env": settings.APP_ENV},
        )
