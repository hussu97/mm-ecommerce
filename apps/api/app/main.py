from __future__ import annotations

import logging
import logging.config
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
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

    _handler = logging.StreamHandler()
    _handler.setFormatter(
        JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "severity"},
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    logging.root.handlers = [_handler]
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

app = FastAPI(
    title="Melting Moments API",
    description="Backend API for Melting Moments Cakes — UAE artisanal bakery",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
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

# 2. CORS — restrict to specific methods and headers
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Session-Id"],
)


# 3. Request size limit (10 MB)
class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 10 * 1024 * 1024:
            return JSONResponse(
                status_code=413, content={"detail": "Request body too large"}
            )
        return await call_next(request)


app.add_middleware(MaxBodySizeMiddleware)

# 4. Request ID + Logging (innermost of the add_middleware stack)
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
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred"},
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(api_router, prefix="/api/v1")

# ---------------------------------------------------------------------------
# System endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["System"], summary="Health check")
async def health(db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ok", "service": "mm-api", "env": settings.APP_ENV}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "service": "mm-api", "env": settings.APP_ENV},
        )
