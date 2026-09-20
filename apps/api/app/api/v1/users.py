from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import search as search_text
from app.core.deps import get_db
from app.core.exceptions import NotFoundError
from app.core.permissions import require
from app.core.phone import normalise_phone
from app.models.admin_passkey import AdminPasskey
from app.models.customer_cache import CustomerCache, CustomerOrderCache
from app.models.order import Order
from app.models.user import User as UserModel
from app.schemas.customer import (
    CustomerOrderHistoryRow,
    CustomerSummary,
    PaginatedCustomerOrders,
    PaginatedCustomers,
)
from app.services import customer_service

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
    name = (order.customer_name or "").strip()
    if name:
        return name
    snapshot = order.shipping_address_snapshot or {}
    name = " ".join(
        str(snapshot.get(key) or "").strip() for key in ("first_name", "last_name")
    )
    return name or None


@router.get("/admin/all", response_model=PaginatedCustomers)
async def list_customers(
    search: str | None = Query(None, description="Search by name, email, or phone"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(require("customers.read")),
) -> PaginatedCustomers:
    """List the deduplicated customer directory across every MM order channel."""
    await customer_service.refresh_if_dirty(db)
    base = select(CustomerCache)
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
    rows = (
        await db.scalars(
            base.order_by(
                CustomerCache.latest_order_at.desc().nullslast(),
                CustomerCache.created_at.desc(),
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).all()
    return PaginatedCustomers(
        items=[
            CustomerSummary(
                id=str(row.id),
                name=row.name,
                email=row.email,
                phone=row.phone,
                phone_country=row.phone_country,
                order_count=row.order_count,
                earliest_order_at=(
                    row.earliest_order_at.isoformat() if row.earliest_order_at else None
                ),
                latest_order_at=(
                    row.latest_order_at.isoformat() if row.latest_order_at else None
                ),
                total_revenue=float(row.total_revenue),
                aov=float(row.aov),
            )
            for row in rows
        ],
        total=total,
        page=page,
        per_page=per_page,
        pages=max(1, (total + per_page - 1) // per_page),
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
                order_channel=_channel_label(order),
                order_value=float(order.total),
            )
            for order in rows
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
