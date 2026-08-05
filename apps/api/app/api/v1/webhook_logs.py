"""
Reading back what the couriers sent us.

Shaped like `email_logs`, including its lesson: the id columns are typed as the
`UUID` they are rather than as `str`, because Pydantic v2 will not coerce one
and these come straight off the ORM row. Getting that wrong made every response
from the audit endpoint a 500 while its table filled up behind it.

The list deliberately omits `payload` and `result`. A tracking ping's body is a
few hundred bytes, a page is up to two thousand rows, and nobody scanning a list
is reading JSON — so the bodies are fetched one row at a time from
`GET /webhook-logs/{id}`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_db
from app.models.user import User
from app.models.webhook_log import WebhookLog

router = APIRouter()


class WebhookLogItem(BaseModel):
    """One request, as the list shows it — everything but the bodies."""

    id: uuid.UUID
    provider: str
    endpoint: str
    received_at: datetime
    remote_ip: str | None = None
    api_key_fingerprint: str | None = None
    signature_valid: bool | None = None
    event_type: str | None = None
    order_number: str | None = None
    courier_order_id: str | None = None
    matched: bool | None = None
    error: str | None = None
    http_status: int | None = None
    duration_ms: int | None = None

    model_config = {"from_attributes": True}


class WebhookLogDetail(WebhookLogItem):
    """One request with the bodies, for the row somebody actually opened."""

    payload: dict | list | None = None
    result: dict | list | None = None


class PaginatedWebhookLogs(BaseModel):
    items: list[WebhookLogItem]
    total: int
    page: int
    per_page: int
    pages: int


@router.get("", response_model=PaginatedWebhookLogs)
async def list_webhook_logs(
    provider: str | None = Query(None, description="lalamove | noon_send"),
    endpoint: str | None = Query(None, description="status | tracking"),
    event_type: str | None = Query(None, description="Their word for it"),
    order_number: str | None = Query(None, description="Search by order number"),
    courier_order_id: str | None = Query(None, description="Their booking id"),
    matched: bool | None = Query(
        None, description="Whether the push found an order at all"
    ),
    errors_only: bool = Query(False, description="Only requests that went wrong"),
    date_from: datetime | None = Query(None, description="From this datetime (UTC)"),
    date_to: datetime | None = Query(None, description="Up to this datetime (UTC)"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
) -> PaginatedWebhookLogs:
    """Every inbound courier webhook, newest first (admin only)."""
    stmt = select(WebhookLog)

    if provider:
        stmt = stmt.where(WebhookLog.provider == provider)
    if endpoint:
        stmt = stmt.where(WebhookLog.endpoint == endpoint)
    if event_type:
        stmt = stmt.where(WebhookLog.event_type == event_type)
    if order_number:
        stmt = stmt.where(WebhookLog.order_number.ilike(f"%{order_number}%"))
    if courier_order_id:
        stmt = stmt.where(WebhookLog.courier_order_id.ilike(f"%{courier_order_id}%"))
    if matched is not None:
        stmt = stmt.where(WebhookLog.matched.is_(matched))
    if errors_only:
        # An unmatched push is a problem as much as a raised one — it means they
        # told us about an order we have no record of, or we lost the id.
        stmt = stmt.where(WebhookLog.error.isnot(None) | WebhookLog.matched.is_(False))
    if date_from:
        stmt = stmt.where(WebhookLog.received_at >= date_from)
    if date_to:
        stmt = stmt.where(WebhookLog.received_at <= date_to)

    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()

    stmt = (
        stmt.order_by(WebhookLog.received_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    items = (await db.execute(stmt)).scalars().all()

    return PaginatedWebhookLogs(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=max(1, (total + per_page - 1) // per_page),
    )


@router.get("/providers", response_model=dict)
async def webhook_log_facets(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
) -> dict:
    """
    The values actually present, so the filters offer real options.

    Read from the table rather than hard-coded: a push from something we have
    not integrated is exactly what this table exists to surface, and a filter
    list written in the frontend could not show it.
    """
    rows = (
        await db.execute(
            select(WebhookLog.provider, WebhookLog.endpoint, WebhookLog.event_type)
            .distinct()
            .limit(500)
        )
    ).all()
    return {
        "providers": sorted({r[0] for r in rows if r[0]}),
        "endpoints": sorted({r[1] for r in rows if r[1]}),
        "event_types": sorted({r[2] for r in rows if r[2]}),
    }


@router.get("/{log_id}", response_model=WebhookLogDetail)
async def get_webhook_log(
    log_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
) -> WebhookLogDetail:
    """One request with its bodies, exactly as they arrived."""
    row = await db.get(WebhookLog, log_id)
    if row is None:
        raise HTTPException(404, "No such webhook log")
    return WebhookLogDetail.model_validate(row)
