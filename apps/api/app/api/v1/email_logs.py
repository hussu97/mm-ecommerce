from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_db
from app.models.email_log import EmailLog
from app.models.user import User

router = APIRouter()


class EmailLogItem(BaseModel):
    id: str
    template: str
    recipient: str
    subject: str
    order_number: str | None
    status: str
    resend_id: str | None
    error: str | None
    sent_at: datetime

    model_config = {"from_attributes": True}


class PaginatedEmailLogs(BaseModel):
    items: list[EmailLogItem]
    total: int
    page: int
    per_page: int
    pages: int


@router.get("/admin/all", response_model=PaginatedEmailLogs)
async def list_email_logs(
    status: str | None = Query(
        None, description="Filter by status: sent, failed, skipped"
    ),
    template: str | None = Query(None, description="Filter by template name"),
    recipient: str | None = Query(None, description="Search by recipient email"),
    order_number: str | None = Query(None, description="Search by order number"),
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
):
    """List email send history with filters (admin only)."""
    stmt = select(EmailLog)

    if status:
        stmt = stmt.where(EmailLog.status == status)
    if template:
        stmt = stmt.where(EmailLog.template == template)
    if recipient:
        stmt = stmt.where(EmailLog.recipient.ilike(f"%{recipient}%"))
    if order_number:
        stmt = stmt.where(EmailLog.order_number.ilike(f"%{order_number}%"))
    if date_from:
        stmt = stmt.where(EmailLog.sent_at >= date_from)
    if date_to:
        stmt = stmt.where(EmailLog.sent_at <= date_to)

    total_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = total_result.scalar_one()

    stmt = stmt.order_by(EmailLog.sent_at.desc())
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(stmt)
    items = result.scalars().all()

    pages = max(1, (total + per_page - 1) // per_page)
    return PaginatedEmailLogs(
        items=items, total=total, page=page, per_page=per_page, pages=pages
    )
