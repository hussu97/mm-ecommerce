"""
The register API — pos.meltingmomentscakes.com.

A separate application from the storefront/admin API, on its own hostname and
in its own container, sharing the codebase and the database but not the route
table. Two reasons it is worth the extra process:

  * A terminal on a shop counter should not be able to reach the customer
    accounts, the CMS or the bulk import endpoints. Narrowing the surface is
    worth more than any amount of per-endpoint checking.
  * Tills keep trading while the website is being deployed, restarted or
    hammered by a campaign, because they are no longer the same process.

    uvicorn app.pos_main:app
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.api.v1.pos_router import pos_api_router
from app.app_setup import (
    add_system_endpoints,
    configure,
    configure_observability,
    make_lifespan,
)
from app.core.config import settings

# Structured logging and Sentry, before the app exists, so a failure during
# construction is still reported.
configure_observability(service="mm-pos-api")

logger = logging.getLogger("mm.pos")

_docs_url = None if settings.is_production else "/docs"

app = FastAPI(
    title="Melting Moments POS API",
    description="Register API for the Melting Moments point of sale",
    version="0.1.0",
    docs_url=_docs_url,
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
    # The storefront app owns the i18n seed; two processes racing the same
    # upsert on boot buys nothing and can deadlock.
    lifespan=make_lifespan("Melting Moments POS API", seed=False),
)

# Read by the device-token dependency: a paired terminal authenticates here
# and nowhere else.
app.state.is_pos_app = True

configure(
    app,
    allowed_hosts=settings.POS_ALLOWED_HOSTS,
    cors_origins=settings.POS_CORS_ORIGINS,
)
add_system_endpoints(app, service="mm-pos-api")

app.include_router(pos_api_router, prefix="/api/v1")
