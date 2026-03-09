from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import (
    get_admin_user,
    get_current_active_user,
    get_db,
    get_optional_user,
)
from app.models.order import Order, OrderStatusEnum
from app.models.user import User
from app.schemas.order import (
    OrderCreate,
    OrderListResponse,
    OrderResponse,
    OrderStatusUpdate,
)
from app.core.cache import cache_delete_pattern
from app.services import email_service, order_service

router = APIRouter()


class PaginatedOrders(BaseModel):
    items: list[OrderListResponse]
    total: int
    page: int
    per_page: int
    pages: int


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    data: OrderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
):
    """
    Create a new order from the current cart.
    For authenticated users, the cart is identified by user_id.
    For guests, provide session_id in the request body.
    """
    user_id = current_user.id if current_user else None
    order = await order_service.create_order(db, data, user_id)
    await cache_delete_pattern("analytics:*")
    return order


@router.get("", response_model=PaginatedOrders)
async def list_my_orders(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get the current user's orders, paginated."""
    items, total = await order_service.get_user_orders(
        db, current_user.id, page, per_page
    )
    pages = max(1, (total + per_page - 1) // per_page)
    return PaginatedOrders(
        items=items, total=total, page=page, per_page=per_page, pages=pages
    )


@router.get("/admin/all", response_model=PaginatedOrders)
async def list_all_orders(
    status: OrderStatusEnum | None = Query(None),
    search: str | None = Query(None, description="Filter by order number or email"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """List all orders with optional filters (admin only)."""
    items, total = await order_service.get_all_admin(
        db, status=status, search=search, page=page, per_page=per_page
    )
    pages = max(1, (total + per_page - 1) // per_page)
    return PaginatedOrders(
        items=items, total=total, page=page, per_page=per_page, pages=pages
    )


class TrackOrderRequest(BaseModel):
    order_number: str
    email: str


@router.post("/track")
async def track_order(data: TrackOrderRequest, db: AsyncSession = Depends(get_db)):
    """Public endpoint to look up order status by order number + email."""
    stmt = (
        select(Order)
        .options(selectinload(Order.items))
        .where(
            Order.order_number == data.order_number,
            Order.email == data.email.lower().strip(),
        )
    )
    order = (await db.execute(stmt)).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found. Check your order number and email.")
    return {
        "order_number": order.order_number,
        "status": order.status.value,
        "delivery_method": order.delivery_method.value,
        "items_count": len(order.items),
        "created_at": order.created_at.strftime("%Y-%m-%d"),
    }


@router.get("/{order_number}", response_model=OrderResponse)
async def get_order(
    order_number: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
):
    """Get an order by order number. Authenticated users can only view their own orders."""
    user_id = current_user.id if current_user else None
    is_admin = current_user.is_admin if current_user else False
    return await order_service.get_by_order_number(
        db, order_number, user_id=user_id, admin=is_admin
    )


@router.put("/{order_number}/status", response_model=OrderResponse)
async def update_order_status(
    order_number: str,
    data: OrderStatusUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Update order status with validated transitions (admin only). Triggers email notification."""
    order = await order_service.update_status(
        db, order_number, data.status, data.admin_notes
    )

    # Send email inline (never raises — safe to await before response).
    # Background tasks can be silently dropped on Cloud Run / serverless.
    if data.status == OrderStatusEnum.CONFIRMED:
        await email_service.send_order_confirmation(order)
    elif data.status == OrderStatusEnum.PACKED:
        await email_service.send_order_packed(order)
    elif data.status == OrderStatusEnum.CANCELLED:
        await email_service.send_order_cancelled(order)
    elif data.status == OrderStatusEnum.PAYMENT_FAILED:
        await email_service.send_payment_failed(order)

    return order
