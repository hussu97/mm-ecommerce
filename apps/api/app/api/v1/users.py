from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_db
from app.models.order import Order, OrderStatusEnum
from app.models.user import User as UserModel

router = APIRouter()


# ─── Schemas ──────────────────────────────────────────────────────────────────

class CustomerSummary(BaseModel):
    id: str
    email: str
    first_name: str
    last_name: str
    phone: str | None
    order_count: int
    total_spent: float
    created_at: str


class PaginatedCustomers(BaseModel):
    items: list[CustomerSummary]
    total: int
    page: int
    per_page: int
    pages: int


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/admin/all", response_model=PaginatedCustomers)
async def list_customers(
    search: str | None = Query(None, description="Search by email or name"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_admin_user),
):
    """List registered (non-guest, non-admin) customers with order stats."""
    # Subquery: order count + total spent per user (excluding cancelled)
    order_subq = (
        select(
            Order.user_id,
            func.count(Order.id).label("order_count"),
            func.coalesce(func.sum(Order.total), 0).label("total_spent"),
        )
        .where(Order.status != OrderStatusEnum.CANCELLED)
        .group_by(Order.user_id)
        .subquery()
    )

    base = (
        select(
            UserModel,
            func.coalesce(order_subq.c.order_count, 0).label("order_count"),
            func.coalesce(order_subq.c.total_spent, 0).label("total_spent"),
        )
        .outerjoin(order_subq, order_subq.c.user_id == UserModel.id)
        .where(
            UserModel.is_guest == False,  # noqa: E712
            UserModel.is_admin == False,  # noqa: E712
        )
    )

    if search:
        base = base.where(
            UserModel.email.ilike(f"%{search}%")
            | UserModel.first_name.ilike(f"%{search}%")
            | UserModel.last_name.ilike(f"%{search}%")
        )

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0

    offset = (page - 1) * per_page
    rows = (
        await db.execute(
            base.order_by(UserModel.created_at.desc()).offset(offset).limit(per_page)
        )
    ).all()

    items = [
        CustomerSummary(
            id=str(row.User.id),
            email=row.User.email,
            first_name=row.User.first_name,
            last_name=row.User.last_name,
            phone=row.User.phone,
            order_count=int(row.order_count),
            total_spent=float(row.total_spent),
            created_at=row.User.created_at.isoformat(),
        )
        for row in rows
    ]

    pages = max(1, (total + per_page - 1) // per_page)
    return PaginatedCustomers(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )
