from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.permissions import require
from app.models.audit_log import AuditLog
from app.models.user import User

router = APIRouter()


class AuditLogItem(BaseModel):
    """
    One row, as the admin reads it.

    The id columns are typed `UUID` rather than `str` because that is what they
    are. Pydantic v2 does not coerce a `UUID` into a `str` field — it raises —
    and since these are read straight off the ORM row, every single response
    from this endpoint was a 500 and the audit screen was permanently empty
    while the table filled up behind it. They still serialise to strings in the
    JSON, so nothing downstream changes.
    """

    id: uuid.UUID
    action: str
    entity_type: str
    #: A `varchar`, not a UUID, whatever the name suggests — it holds whichever
    #: identifier a reader would recognise, so an order logs `MM-20260605-001`
    #: and a product logs its id. Typed as a UUID it took down every page that
    #: contained an order.
    entity_id: str
    #: Nullable in the table. Typed as such, or a row logged without a label
    #: takes the whole page down with it rather than showing a blank cell.
    entity_label: str | None = None
    admin_id: uuid.UUID | None = None
    admin_email: str | None = None
    changes: dict | None = None
    ip_address: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedAuditLogs(BaseModel):
    items: list[AuditLogItem]
    total: int
    page: int
    per_page: int
    pages: int


@router.get("", response_model=PaginatedAuditLogs)
async def list_audit_logs(
    action: str | None = Query(
        None, description="Filter by action: CREATE, UPDATE, DELETE, STATUS_CHANGE"
    ),
    entity_type: str | None = Query(
        None, description="Filter by entity type: product, order, category, promo_code"
    ),
    admin_id: uuid.UUID | None = Query(None, description="Filter by admin user ID"),
    search: str | None = Query(
        None, description="Search by entity label or admin email"
    ),
    date_from: datetime | None = Query(
        None, description="Filter logs from this datetime (UTC)"
    ),
    date_to: datetime | None = Query(
        None, description="Filter logs up to this datetime (UTC)"
    ),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("admin.logs.read")),
) -> PaginatedAuditLogs:
    """List admin audit log entries with filters (admin only)."""
    stmt = select(AuditLog)

    if action:
        stmt = stmt.where(AuditLog.action == action.upper())
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type.lower())
    if admin_id:
        stmt = stmt.where(AuditLog.admin_id == admin_id)
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            AuditLog.entity_label.ilike(pattern) | AuditLog.admin_email.ilike(pattern)
        )
    if date_from:
        stmt = stmt.where(AuditLog.created_at >= date_from)
    if date_to:
        stmt = stmt.where(AuditLog.created_at <= date_to)

    total_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = total_result.scalar_one()

    stmt = stmt.order_by(AuditLog.created_at.desc())
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(stmt)
    items = result.scalars().all()

    pages = max(1, (total + per_page - 1) // per_page)
    return PaginatedAuditLogs(
        items=items, total=total, page=page, per_page=per_page, pages=pages
    )


@router.get("/{log_id}", response_model=AuditLogItem)
async def get_audit_log(
    log_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("admin.logs.read")),
) -> AuditLog:
    """Get a single audit log entry (admin only)."""
    from app.core.exceptions import NotFoundError

    result = await db.get(AuditLog, log_id)
    if not result:
        raise NotFoundError("Audit log entry not found")
    return result
