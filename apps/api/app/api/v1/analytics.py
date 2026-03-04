from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_db
from app.models.order import Order, OrderItem, OrderStatusEnum
from app.models.user import User

router = APIRouter()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _date_range(
    start_date: Optional[date],
    end_date: Optional[date],
) -> tuple[date, date]:
    if not end_date:
        end_date = date.today()
    if not start_date:
        start_date = end_date - timedelta(days=30)
    return start_date, end_date


# ─── Response schemas ─────────────────────────────────────────────────────────

class OverviewResponse(BaseModel):
    total_revenue: float
    total_orders: int
    avg_order_value: float
    total_customers: int
    revenue_growth: float
    orders_growth: float


class RevenuePoint(BaseModel):
    date: str
    revenue: float


class OrdersPoint(BaseModel):
    date: str
    count: int


class TopProduct(BaseModel):
    product_name: str
    variant_name: str
    revenue: float
    quantity: int


class FunnelData(BaseModel):
    created: int
    confirmed: int
    packed: int
    cancelled: int
    conversion_rate: float


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/overview", response_model=OverviewResponse)
async def get_overview(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Revenue, orders, customers, and growth vs prior period."""
    start, end = _date_range(start_date, end_date)

    stmt = select(
        func.coalesce(func.sum(Order.total), 0).label("revenue"),
        func.count(Order.id).label("orders"),
        func.count(func.distinct(Order.user_id)).label("customers"),
    ).where(
        Order.status != OrderStatusEnum.CANCELLED,
        func.date(Order.created_at) >= start,
        func.date(Order.created_at) <= end,
    )
    result = (await db.execute(stmt)).one()

    total_revenue = float(result.revenue)
    total_orders = int(result.orders)
    total_customers = int(result.customers)
    avg = total_revenue / total_orders if total_orders else 0.0

    # Prior period for growth comparison
    period_days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=period_days - 1)

    prev_stmt = select(
        func.coalesce(func.sum(Order.total), 0).label("revenue"),
        func.count(Order.id).label("orders"),
    ).where(
        Order.status != OrderStatusEnum.CANCELLED,
        func.date(Order.created_at) >= prev_start,
        func.date(Order.created_at) <= prev_end,
    )
    prev = (await db.execute(prev_stmt)).one()
    prev_rev = float(prev.revenue)
    prev_orders = int(prev.orders)

    rev_growth = ((total_revenue - prev_rev) / prev_rev * 100) if prev_rev else 0.0
    orders_growth = ((total_orders - prev_orders) / prev_orders * 100) if prev_orders else 0.0

    return OverviewResponse(
        total_revenue=total_revenue,
        total_orders=total_orders,
        avg_order_value=round(avg, 2),
        total_customers=total_customers,
        revenue_growth=round(rev_growth, 1),
        orders_growth=round(orders_growth, 1),
    )


@router.get("/revenue", response_model=list[RevenuePoint])
async def get_revenue(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    group_by: str = Query("day", pattern="^(day|week|month)$"),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Daily/weekly/monthly revenue totals."""
    start, end = _date_range(start_date, end_date)
    trunc = func.date_trunc(group_by, Order.created_at)

    stmt = (
        select(trunc.label("period"), func.coalesce(func.sum(Order.total), 0).label("revenue"))
        .where(
            Order.status != OrderStatusEnum.CANCELLED,
            func.date(Order.created_at) >= start,
            func.date(Order.created_at) <= end,
        )
        .group_by("period")
        .order_by("period")
    )
    rows = (await db.execute(stmt)).all()

    return [
        RevenuePoint(
            date=row.period.strftime("%Y-%m-%d") if row.period else "",
            revenue=float(row.revenue),
        )
        for row in rows
    ]


@router.get("/orders-chart", response_model=list[OrdersPoint])
async def get_orders_chart(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    group_by: str = Query("day", pattern="^(day|week|month)$"),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Daily/weekly/monthly order counts."""
    start, end = _date_range(start_date, end_date)
    trunc = func.date_trunc(group_by, Order.created_at)

    stmt = (
        select(trunc.label("period"), func.count(Order.id).label("count"))
        .where(
            func.date(Order.created_at) >= start,
            func.date(Order.created_at) <= end,
        )
        .group_by("period")
        .order_by("period")
    )
    rows = (await db.execute(stmt)).all()

    return [
        OrdersPoint(
            date=row.period.strftime("%Y-%m-%d") if row.period else "",
            count=int(row.count),
        )
        for row in rows
    ]


@router.get("/top-products", response_model=list[TopProduct])
async def get_top_products(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Top products by revenue."""
    start, end = _date_range(start_date, end_date)

    stmt = (
        select(
            OrderItem.product_name,
            OrderItem.variant_name,
            func.coalesce(func.sum(OrderItem.total_price), 0).label("revenue"),
            func.coalesce(func.sum(OrderItem.quantity), 0).label("quantity"),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.status != OrderStatusEnum.CANCELLED,
            func.date(Order.created_at) >= start,
            func.date(Order.created_at) <= end,
        )
        .group_by(OrderItem.product_name, OrderItem.variant_name)
        .order_by(func.sum(OrderItem.total_price).desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    return [
        TopProduct(
            product_name=row.product_name,
            variant_name=row.variant_name,
            revenue=float(row.revenue),
            quantity=int(row.quantity),
        )
        for row in rows
    ]


@router.get("/funnel", response_model=FunnelData)
async def get_funnel(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Order counts by status (funnel)."""
    start, end = _date_range(start_date, end_date)

    stmt = (
        select(Order.status, func.count(Order.id).label("count"))
        .where(
            func.date(Order.created_at) >= start,
            func.date(Order.created_at) <= end,
        )
        .group_by(Order.status)
    )
    rows = (await db.execute(stmt)).all()
    counts: dict[OrderStatusEnum, int] = {row.status: int(row.count) for row in rows}

    created = counts.get(OrderStatusEnum.CREATED, 0)
    confirmed = counts.get(OrderStatusEnum.CONFIRMED, 0)
    packed = counts.get(OrderStatusEnum.PACKED, 0)
    cancelled = counts.get(OrderStatusEnum.CANCELLED, 0)
    total = created + confirmed + packed + cancelled

    conversion_rate = round(packed / total * 100, 1) if total else 0.0

    return FunnelData(
        created=created,
        confirmed=confirmed,
        packed=packed,
        cancelled=cancelled,
        conversion_rate=conversion_rate,
    )
