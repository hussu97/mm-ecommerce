"""The one write path into `aggregator_session`, and a health read.

The ingest itself takes no HTTP — it is a background loop. This router exists so
the bootstrap/warmer worker (which runs a browser elsewhere, off the app VM) can
hand a freshly captured session in over HTTPS, exactly the way the standalone
scraper pushed to `/api/ingest/bulk`. It authenticates on a shared bearer
(`AGGREGATOR_SESSION_PUSH_TOKEN`) rather than a user login, because the caller
is a machine, not a person — and an unset token closes the path rather than
leaving it open.

The health read is behind the same reporting permission the aggregator dashboard
uses, so an operator can see which sessions are live without holding the push
token.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_db
from app.core.exceptions import (
    BadRequestError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from app.core.permissions import require
from app.models.aggregator import AGGREGATOR_CHANNELS, AggregatorSession
from app.schemas.aggregator import AggregatorSessionPush, AggregatorSessionResponse
from app.services.aggregators import crypto, session_store

router = APIRouter()


def _require_push_token(authorization: str | None = Header(None)) -> None:
    """Verify the worker's shared bearer in constant time.

    An unset `AGGREGATOR_SESSION_PUSH_TOKEN` is a closed door, not an open one —
    the same fail-closed posture the ingest flag takes.
    """
    expected = settings.AGGREGATOR_SESSION_PUSH_TOKEN
    if not expected:
        raise UnauthorizedError("aggregator session push is not configured")
    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    if not hmac.compare_digest(presented, expected):
        raise UnauthorizedError("invalid aggregator session push token")


@router.post("/session", response_model=AggregatorSessionResponse)
async def push_session(
    body: AggregatorSessionPush,
    _: None = Depends(_require_push_token),
    db: AsyncSession = Depends(get_db),
) -> AggregatorSession:
    """Store (or replace) the session for one channel, sealed at rest."""
    if body.channel not in AGGREGATOR_CHANNELS:
        raise BadRequestError(f"unknown aggregator channel: {body.channel}")
    if not crypto.is_configured():
        raise ServiceUnavailableError(
            "AGGREGATOR_CONFIG_ENCRYPTION_KEY is unset; cannot store a session"
        )
    return await session_store.upsert_bootstrap(
        db,
        channel=body.channel,
        account_ref=body.account_ref,
        cookies=body.cookies,
        tokens=body.tokens,
        header_profile=body.header_profile,
        token_expires_at=body.token_expires_at,
        cookie_expires_at=body.cookie_expires_at,
    )


@router.get(
    "/sessions",
    response_model=list[AggregatorSessionResponse],
    dependencies=[Depends(require("reports.sales"))],
)
async def list_sessions(
    db: AsyncSession = Depends(get_db),
) -> list[AggregatorSession]:
    """Session health per channel — the monitoring read (no secrets exposed)."""
    rows = await db.scalars(
        select(AggregatorSession).order_by(AggregatorSession.channel)
    )
    return list(rows)
