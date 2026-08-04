from __future__ import annotations

import uuid
from datetime import datetime

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
from app.models.order_delivery import OrderDelivery
from app.models.user import User
from app.schemas.order import (
    OrderCreate,
    OrderListResponse,
    OrderResponse,
    OrderStatusUpdate,
)
from fastapi import Request

from app.core.cache import cache_delete_pattern
from app.models.delivery_polygon import FulfilmentProviderEnum
from app.services import (
    audit_service,
    courier_service,
    email_service,
    lalamove_service,
    noon_send_service,
    order_service,
)

router = APIRouter()


class PaginatedOrders(BaseModel):
    items: list[OrderListResponse]
    total: int
    page: int
    per_page: int
    pages: int


class OrderDeliveryResponse(BaseModel):
    """
    The fulfilment side of an order. **Admin only.**

    Deliberately not folded into `OrderResponse`: that model is served to the
    customer, and we have decided they are not told which courier carries their
    cake. Keeping the two apart makes that a structural guarantee rather than a
    field somebody has to remember not to add.
    """

    provider: str
    zone_name: str | None
    fee_charged: float | None
    quoted_cost: float | None
    quoted_currency: str | None
    quoted_distance_m: int | None
    cost_total: float | None
    #: Fee minus cost. Negative means this delivery lost money.
    margin: float | None
    courier_order_id: str | None
    courier_status: str | None
    share_link: str | None
    driver_name: str | None
    driver_phone: str | None
    driver_plate: str | None
    pod_status: str | None
    pod_image_url: str | None
    booked_at: datetime | None
    picked_up_at: datetime | None
    delivered_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    last_error: str | None
    needs_attention: bool

    @classmethod
    def of(cls, d: OrderDelivery) -> "OrderDeliveryResponse":
        cost = d.cost_total if d.cost_total is not None else d.quoted_cost
        margin = (
            float(d.fee_charged) - float(cost)
            if d.fee_charged is not None and cost is not None
            else None
        )
        return cls(
            provider=d.provider,
            zone_name=d.zone_name,
            fee_charged=float(d.fee_charged) if d.fee_charged is not None else None,
            quoted_cost=float(d.quoted_cost) if d.quoted_cost is not None else None,
            quoted_currency=d.quoted_currency,
            quoted_distance_m=d.quoted_distance_m,
            cost_total=float(d.cost_total) if d.cost_total is not None else None,
            margin=margin,
            courier_order_id=d.courier_order_id,
            courier_status=d.courier_status,
            share_link=d.share_link,
            driver_name=d.driver_name,
            driver_phone=d.driver_phone,
            driver_plate=d.driver_plate,
            pod_status=d.pod_status,
            pod_image_url=d.pod_image_url,
            booked_at=d.booked_at,
            picked_up_at=d.picked_up_at,
            delivered_at=d.delivered_at,
            cancelled_at=d.cancelled_at,
            cancel_reason=d.cancel_reason,
            last_error=d.last_error,
            needs_attention=d.needs_attention,
        )


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
    order = await order_service.create_order(
        db, data, user_id, fallback_email=current_user.email if current_user else None
    )
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
    search: str | None = Query(
        None, description="Order number, email, customer name or phone"
    ),
    channel: str | None = Query(
        None,
        pattern="^(online|counter)$",
        description="`online` for the storefront, `counter` for the till. "
        "Omit for both — they are one ledger.",
    ),
    branch_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Every order, from either channel (admin only)."""
    items, total = await order_service.get_all_admin(
        db,
        status=status,
        search=search,
        page=page,
        per_page=per_page,
        channel=channel,
        branch_id=branch_id,
    )
    pages = max(1, (total + per_page - 1) // per_page)
    return PaginatedOrders(
        items=items, total=total, page=page, per_page=per_page, pages=pages
    )


@router.post("/{order_number}/delivery/refresh", response_model=OrderDeliveryResponse)
async def refresh_order_delivery(
    order_number: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """
    Ask the courier where this order actually is.

    Only noon Send needs it, and it needs it badly: their statuses arrive by
    push only, they do not retry a delivery that failed, and there is nothing to
    replay from their side. Lalamove retries for a day and Stripe can be resent;
    a lost noon Send push is simply lost, and without this the shop would still
    be looking at "assigned" the next morning.
    """
    order = (
        (await db.execute(select(Order).where(Order.order_number == order_number)))
        .scalars()
        .first()
    )
    if order is None:
        raise HTTPException(404, f"Order '{order_number}' not found")

    delivery = await lalamove_service.get_delivery(db, order.id)
    if delivery is None:
        raise HTTPException(404, f"No delivery recorded for order '{order_number}'")
    if delivery.provider != FulfilmentProviderEnum.NOON_SEND.value:
        raise HTTPException(
            400,
            "Only noon Send needs asking — Lalamove pushes its own updates and "
            "retries them for a day.",
        )

    return OrderDeliveryResponse.of(
        await noon_send_service.refresh(db, order.id) or delivery
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
    email: str | None = Query(
        None, description="Order email — proof of ownership for unauthenticated calls"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
):
    """
    Get an order by order number. Authenticated users can only view their own
    orders; unauthenticated callers must supply the order's email as proof
    (same scheme as /orders/track).
    """
    user_id = current_user.id if current_user else None
    is_admin = current_user.is_admin if current_user else False
    return await order_service.get_by_order_number(
        db, order_number, user_id=user_id, admin=is_admin, email=email
    )


@router.put("/{order_number}/status", response_model=OrderResponse)
async def update_order_status(
    request: Request,
    order_number: str,
    data: OrderStatusUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Update order status with validated transitions (admin only). Triggers email notification."""
    old_order = await order_service.get_by_order_number(db, order_number, admin=True)
    old_status = old_order.status.value

    order = await order_service.update_status(
        db, order_number, data.status, data.admin_notes
    )

    # Send email inline (never raises — safe to await before response).
    # Background tasks can be silently dropped on Cloud Run / serverless.
    if data.status == OrderStatusEnum.CONFIRMED:
        await email_service.send_order_confirmation(order)
        await email_service.send_owner_order_notification(order)
    elif data.status == OrderStatusEnum.PACKED:
        await email_service.send_order_packed(order)
    elif data.status == OrderStatusEnum.CANCELLED:
        await email_service.send_order_cancelled(order)
    elif data.status == OrderStatusEnum.PAYMENT_FAILED:
        await email_service.send_payment_failed(order)
    elif data.status == OrderStatusEnum.REFUNDED:
        await email_service.send_refund_notification(order)

    await audit_service.log_action(
        db,
        action="STATUS_CHANGE",
        entity_type="order",
        entity_id=order_number,
        entity_label=order_number,
        admin=admin,
        changes={
            "from": old_status,
            "to": data.status.value,
            **({"admin_notes": data.admin_notes} if data.admin_notes else {}),
        },
        request=request,
    )

    return order


# ── Fulfilment (admin only) ───────────────────────────────────────────────────


async def _load_delivery(db: AsyncSession, order_number: str) -> OrderDelivery:
    result = await db.execute(
        select(OrderDelivery)
        .join(Order, Order.id == OrderDelivery.order_id)
        .where(Order.order_number == order_number)
    )
    delivery = result.scalars().first()
    if delivery is None:
        raise HTTPException(404, f"No delivery recorded for order '{order_number}'")
    return delivery


@router.get("/{order_number}/delivery", response_model=OrderDeliveryResponse)
async def get_order_delivery(
    order_number: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Who is carrying this order, where it is, and what it cost us."""
    return OrderDeliveryResponse.of(await _load_delivery(db, order_number))


@router.post("/{order_number}/delivery/dispatch", response_model=OrderDeliveryResponse)
async def dispatch_order_delivery(
    order_number: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """
    Book the courier again after a failed or abandoned dispatch.

    The normal path books automatically when the order is packed. This exists
    for the cases that path cannot handle on its own: the wallet was empty, the
    courier was unreachable, or every driver rejected the job.
    """
    result = await db.execute(select(Order).where(Order.order_number == order_number))
    order = result.scalars().first()
    if order is None:
        raise HTTPException(404, f"Order '{order_number}' not found")

    delivery = await courier_service.dispatch(db, order)
    if delivery is None:
        raise HTTPException(404, f"No delivery recorded for order '{order_number}'")

    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="order_delivery",
        entity_id=order_number,
        entity_label=order_number,
        admin=admin,
        changes={
            "courier_order_id": delivery.courier_order_id,
            "error": delivery.last_error,
        },
        request=request,
    )
    return OrderDeliveryResponse.of(delivery)
