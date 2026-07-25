from __future__ import annotations

import logging
import logging.config

from fastapi import Depends, FastAPI

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from app.api.v1.router import api_router
from app.app_setup import add_system_endpoints, configure, make_lifespan
from app.core.config import settings
from app.core.deps import get_admin_user

# ---------------------------------------------------------------------------
# Sentry — initialised before app creation so all errors are captured
# ---------------------------------------------------------------------------

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT,
        integrations=[
            FastApiIntegration(),
            SqlalchemyIntegration(),
        ],
        traces_sample_rate=(
            settings.SENTRY_TRACES_SAMPLE_RATE if settings.is_production else 1.0
        ),
        send_default_pii=False,
    )

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
    lifespan=make_lifespan("Melting Moments API", seed=True),
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
