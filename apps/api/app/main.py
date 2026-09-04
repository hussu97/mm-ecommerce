from __future__ import annotations

import logging

from fastapi import Depends, FastAPI

from app.api.v1.router import api_router
from app.app_setup import (
    add_system_endpoints,
    configure,
    configure_observability,
    make_lifespan,
)
from app.core.config import settings
from app.core.deps import get_admin_user

configure_observability(service="mm-api")

logger = logging.getLogger("mm.api")

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


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
    lifespan=make_lifespan("Melting Moments API", seed=True, run_scheduler=True),
)

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

configure(
    app,
    allowed_hosts=settings.ALLOWED_HOSTS,
    cors_origins=settings.CORS_ORIGINS,
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(api_router, prefix="/api/v1")


@app.post(
    "/api/v1/sentry-debug",
    tags=["System"],
    summary="Force a Sentry test exception",
    include_in_schema=False,
)
async def sentry_debug(_admin=Depends(get_admin_user)) -> None:
    raise RuntimeError("Sentry debug exception from mm-backend")


add_system_endpoints(app, service="mm-api")
