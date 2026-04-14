from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_db
from app.models.audit_log import AuditLog
from app.models.user import User

router = APIRouter()


class AuditLogItem(BaseModel):
    id: str
    action: str
    entity_type: str
    entity_id: str
    entity_label: str
    admin_id: str
    admin_email: str
    changes: dict | None
    ip_address: str | None
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
    _admin: User = Depends(get_admin_user),
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
    _admin: User = Depends(get_admin_user),
) -> AuditLog:
    """Get a single audit log entry (admin only)."""
    from app.core.exceptions import NotFoundError

    result = await db.get(AuditLog, log_id)
    if not result:
        raise NotFoundError("Audit log entry not found")
    return result
