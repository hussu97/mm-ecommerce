from __future__ import annotations

import logging
import logging.config

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.limiter import limiter

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO if settings.is_production else logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("mm.api")

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
async def health() -> dict:
    return {"status": "ok", "service": "mm-api", "env": settings.APP_ENV}


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def startup() -> None:
    if settings.is_production:
        if settings.SECRET_KEY == "change-me-in-production-use-a-long-random-string-here":
            raise RuntimeError("SECRET_KEY must be changed in production")
        if not settings.STRIPE_WEBHOOK_SECRET:
            raise RuntimeError("STRIPE_WEBHOOK_SECRET must be set in production")
    logger.info("Melting Moments API starting up [env=%s]", settings.APP_ENV)


@app.on_event("shutdown")
async def shutdown() -> None:
    logger.info("Melting Moments API shutting down")
