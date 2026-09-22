from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import search as search_text
from app.core.deps import get_db
from app.core.exceptions import NotFoundError
from app.core.permissions import require
from app.core.phone import normalise_phone
from app.models.admin_passkey import AdminPasskey
from app.models.customer_cache import CustomerCache, CustomerOrderCache
from app.models.order import Order
from app.models.user import User as UserModel
from app.schemas.courier import CourierBadge
from app.schemas.customer import (
    CustomerDeliveryAreas,
    CustomerOrderHistoryRow,
    CustomerSummary,
    PaginatedCustomerOrders,
    PaginatedCustomers,
)
from app.services import customer_delivery_area_service, customer_service
from app.services.orders import order_query
from app.services.pos import business_day_service

router = APIRouter()


class AdminUserSummary(BaseModel):
    id: str
    email: str
    phone: str | None
    is_active: bool
    is_superadmin: bool
    passkey_count: int
    created_at: str


def _channel_label(order: Order) -> str:
    if order.source == "aggregator":
        return order.aggregator_channel or "Aggregator"
    if order.source == "cashier":
        return "Counter"
    return "Website"


def _order_name(order: Order) -> str | None:
    name = customer_service.normalise_customer_name(order.customer_name)
    if name:
        return name
    snapshot = order.shipping_address_snapshot or {}
    return customer_service.normalise_customer_name(
        " ".join(
            str(snapshot.get(key) or "").strip() for key in ("first_name", "last_name")
        )
    )


def _channel_for_order(order: Order) -> tuple[str, str | None]:
    """Return the same channel identity the orders list uses for its logo."""
    code = order_query.courier_code_for(
        order.source,
        order.aggregator_channel,
        order.delivery.provider if order.delivery else None,
        order.delivery_method.value,
    )
    return (order_query.courier_label(code) if code else _channel_label(order), code)


@router.get("/admin/all", response_model=PaginatedCustomers)
async def list_customers(
    search: str | None = Query(None, description="Search by name, email, or phone"),
    date_from: str | None = Query(
        None, description="ISO date; with date_to, filters and rolls up by order date."
    ),
    date_to: str | None = Query(
        None,
        description="ISO date; with date_from, filters and rolls up by order date.",
    ),
    sort_by: Literal[
        "order_count", "earliest_order_at", "latest_order_at", "total_revenue", "aov"
    ] = Query("latest_order_at"),
    sort_direction: Literal["asc", "desc"] = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(require("customers.read")),
) -> PaginatedCustomers:
    """List the deduplicated customer directory across every MM order channel.

    A complete date pair makes the rows and summary metrics reflect orders in
    that order-date window, matching the dashboard and orders list. With no
    range, the precomputed all-time cache is read directly.
    """
    await customer_service.refresh_if_dirty(db)

    bounds = await business_day_service.range_bounds(db, date_from, date_to)
    if bounds is None:
        base = select(CustomerCache)
        fields = {
            "order_count": CustomerCache.order_count,
            "earliest_order_at": CustomerCache.earliest_order_at,
            "latest_order_at": CustomerCache.latest_order_at,
            "total_revenue": CustomerCache.total_revenue,
            "aov": CustomerCache.aov,
        }
    else:
        start, end = bounds
        billable = Order.status != "cancelled"
        order_count = func.coalesce(func.sum(case((billable, 1), else_=0)), 0)
        total_revenue = func.coalesce(
            func.sum(case((billable, Order.total), else_=0)), 0
        )
        metrics = (
            select(
                CustomerOrderCache.customer_id.label("customer_id"),
                order_count.label("order_count"),
                func.min(case((billable, Order.created_at))).label("earliest_order_at"),
                func.max(case((billable, Order.created_at))).label("latest_order_at"),
                total_revenue.label("total_revenue"),
                (total_revenue / func.nullif(order_count, 0)).label("aov"),
            )
            .join(Order, Order.id == CustomerOrderCache.order_id)
            .where(Order.created_at >= start, Order.created_at <= end)
            .group_by(CustomerOrderCache.customer_id)
            .subquery()
        )
        base = select(
            CustomerCache,
            metrics.c.order_count,
            metrics.c.earliest_order_at,
            metrics.c.latest_order_at,
            metrics.c.total_revenue,
            metrics.c.aov,
        ).join(metrics, metrics.c.customer_id == CustomerCache.id)
        fields = {
            "order_count": metrics.c.order_count,
            "earliest_order_at": metrics.c.earliest_order_at,
            "latest_order_at": metrics.c.latest_order_at,
            "total_revenue": metrics.c.total_revenue,
            "aov": metrics.c.aov,
        }
    if search:
        phone_search = normalise_phone(search) or search
        base = base.where(
            or_(
                search_text.contains(CustomerCache.name, search),
                search_text.contains(CustomerCache.email, search),
                search_text.contains(CustomerCache.phone, phone_search),
            )
        )

    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    sort_column = fields[sort_by]
    order_by = (
        sort_column.asc().nullslast()
        if sort_direction == "asc"
        else sort_column.desc().nullslast()
    )
    rows = (
        await db.execute(
            base.order_by(order_by, CustomerCache.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).all()

    def summary(row: object) -> CustomerSummary:
        customer = row[0]
        if bounds is None:
            order_count = customer.order_count
            earliest_order_at = customer.earliest_order_at
            latest_order_at = customer.latest_order_at
            total_revenue = customer.total_revenue
            aov = customer.aov
        else:
            order_count = row[1]
            earliest_order_at = row[2]
            latest_order_at = row[3]
            total_revenue = row[4]
            aov = row[5]
        return CustomerSummary(
            id=str(customer.id),
            name=customer_service.normalise_customer_name(customer.name),
            email=customer.email,
            phone=customer.phone,
            phone_country=customer.phone_country,
            order_count=int(order_count),
            earliest_order_at=(
                earliest_order_at.isoformat() if earliest_order_at else None
            ),
            latest_order_at=(latest_order_at.isoformat() if latest_order_at else None),
            total_revenue=float(total_revenue),
            aov=float(aov or 0),
        )

    return PaginatedCustomers(
        items=[summary(row) for row in rows],
        total=total,
        page=page,
        per_page=per_page,
        pages=max(1, (total + per_page - 1) // per_page),
    )


@router.get("/admin/delivery-areas", response_model=CustomerDeliveryAreas)
async def customer_delivery_areas(
    search: str | None = Query(None, description="Search by name, email, or phone"),
    date_from: str | None = Query(None, description="ISO date"),
    date_to: str | None = Query(None, description="ISO date"),
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(require("customers.read")),
) -> CustomerDeliveryAreas:
    """Show demand density over the current live delivery-zone geometry.

    The same customer/date filter contract as ``/admin/all`` makes the two
    customer tabs one workspace, while map cells stay aggregate-only and never
    expose individual customer coordinates.
    """
    await customer_service.refresh_if_dirty(db)
    bounds = await business_day_service.range_bounds(db, date_from, date_to)
    return CustomerDeliveryAreas.model_validate(
        await customer_delivery_area_service.load(
            db,
            search=search,
            start=bounds[0] if bounds else None,
            end=bounds[1] if bounds else None,
        )
    )


@router.get("/admin/{customer_id}/orders", response_model=PaginatedCustomerOrders)
async def list_customer_orders(
    customer_id: uuid.UUID,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(require("customers.read")),
) -> PaginatedCustomerOrders:
    """Return the actual orders that established one cached customer identity."""
    await customer_service.refresh_if_dirty(db)
    if await db.get(CustomerCache, customer_id) is None:
        raise NotFoundError("Customer not found")

    base = (
        select(Order)
        .join(CustomerOrderCache, CustomerOrderCache.order_id == Order.id)
        .where(CustomerOrderCache.customer_id == customer_id)
    )
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        await db.scalars(
            base.order_by(Order.created_at.desc())
            .options(selectinload(Order.delivery))
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).all()
    return PaginatedCustomerOrders(
        items=[
            CustomerOrderHistoryRow(
                id=str(order.id),
                order_number=order.order_number,
                customer_name=_order_name(order),
                customer_phone=order.customer_phone,
                customer_email=order.email or None,
                order_date=order.created_at.isoformat(),
                order_channel=channel[0],
                order_channel_code=channel[1],
                courier=CourierBadge.for_order(
                    source=order.source,
                    aggregator_channel=order.aggregator_channel,
                    delivery_provider=order.delivery.provider
                    if order.delivery
                    else None,
                ),
                order_value=float(order.total),
            )
            for order in rows
            for channel in [_channel_for_order(order)]
        ],
        total=total,
        page=page,
        per_page=per_page,
        pages=max(1, (total + per_page - 1) // per_page),
    )


@router.get("/admin/admin-users", response_model=list[AdminUserSummary])
async def list_admin_users(
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(require("customers.read")),
) -> list[AdminUserSummary]:
    """List admin users and passkey registration status."""
    passkey_counts = (
        select(
            AdminPasskey.user_id,
            func.count(AdminPasskey.id).label("passkey_count"),
        )
        .group_by(AdminPasskey.user_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                UserModel,
                func.coalesce(passkey_counts.c.passkey_count, 0).label("passkey_count"),
            )
            .outerjoin(passkey_counts, passkey_counts.c.user_id == UserModel.id)
            .where(UserModel.is_admin == True)  # noqa: E712
            .order_by(UserModel.email.asc())
        )
    ).all()
    return [
        AdminUserSummary(
            id=str(row.User.id),
            email=row.User.email,
            phone=row.User.phone,
            is_active=row.User.is_active,
            is_superadmin=row.User.email == "admin@meltingmomentscakes.com",
            passkey_count=int(row.passkey_count),
            created_at=row.User.created_at.isoformat(),
        )
        for row in rows
    ]
