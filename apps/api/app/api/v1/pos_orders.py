"""The POS order engine's HTTP surface, plus the kitchen display feed."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_current_active_user, get_db
from app.core.exceptions import ForbiddenError, NotFoundError
from app.models import (
    Branch,
    KitchenTicket,
    KitchenTicketStatusEnum,
    Order,
    PosOrderStatusEnum,
    PosTable,
    Till,
)
from app.models.base import utcnow
from app.models.user import User
from app.schemas.pos_order import (
    AddItemRequest,
    ApplyChargeRequest,
    ApplyDiscountRequest,
    KitchenTicketResponse,
    KitchenTicketStatusUpdate,
    OpenOrderRequest,
    OrderItemResponse,
    PaymentRequest,
    PosOrderResponse,
    ReturnItemRequest,
    VoidItemRequest,
    VoidOrderRequest,
)
from app.services import crud_service, pos_order_service

router = APIRouter()


def _serialise(order: Order) -> PosOrderResponse:
    payload = PosOrderResponse.model_validate(order)
    payload.customer_id = order.user_id
    payload.amount_paid = order.amount_paid
    payload.balance_due = order.balance_due
    # Voided lines stay in the database for audit but never render on the check.
    payload.items = [
        OrderItemResponse.model_validate(i) for i in order.items if i.status != "void"
    ]
    return payload


async def _require_permission(user: User, permission: str) -> None:
    if not user.can(permission):
        raise ForbiddenError(f"You do not have permission to {permission}")


async def _load(db: AsyncSession, order_id: uuid.UUID) -> Order:
    return await pos_order_service.get_order(db, order_id)


async def _resolve_till(
    db: AsyncSession, till_id: uuid.UUID | None, order: Order | None = None
) -> Till | None:
    target = till_id or (order.till_id if order else None)
    if target is None:
        return None
    till = await db.get(Till, target)
    if till is None:
        raise NotFoundError("Till not found")
    return till


# ─── Lifecycle ────────────────────────────────────────────────────────────────


@router.get("", response_model=list[PosOrderResponse])
async def list_orders(
    branch_id: uuid.UUID | None = None,
    business_date: str | None = None,
    pos_status: str | None = None,
    order_type: str | None = None,
    open_only: bool = False,
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    await _require_permission(user, "orders.read")
    stmt = select(Order).where(Order.is_pos.is_(True))
    if branch_id:
        stmt = stmt.where(Order.branch_id == branch_id)
    if business_date:
        stmt = stmt.where(Order.business_date == business_date)
    if pos_status:
        stmt = stmt.where(Order.pos_status == pos_status)
    if order_type:
        stmt = stmt.where(Order.order_type == order_type)
    if open_only:
        stmt = stmt.where(Order.pos_status.in_(sorted(pos_order_service.OPEN_STATUSES)))
    stmt = (
        stmt.options(
            selectinload(Order.items),
            selectinload(Order.payments),
            selectinload(Order.order_charges),
            selectinload(Order.order_discounts),
            selectinload(Order.order_taxes),
        )
        .order_by(Order.opened_at.desc().nullslast(), Order.created_at.desc())
        .limit(limit)
    )
    orders = list((await db.execute(stmt)).scalars().unique().all())
    return [_serialise(o) for o in orders]


@router.post("", response_model=PosOrderResponse, status_code=status.HTTP_201_CREATED)
async def open_order(
    data: OpenOrderRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    await _require_permission(user, "pos.register.access")
    branch = await crud_service.get_or_404(db, Branch, data.branch_id)
    till = await _resolve_till(db, data.till_id)
    order = await pos_order_service.open_order(
        db,
        branch=branch,
        user=user,
        order_type=data.order_type,
        till=till,
        device_id=data.device_id,
        table_id=data.table_id,
        guests=data.guests,
        customer_id=data.customer_id,
        customer_name=data.customer_name,
        customer_phone=data.customer_phone,
        notes=data.notes,
        source=data.source,
    )
    return _serialise(order)


@router.get("/{order_id}", response_model=PosOrderResponse)
async def get_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    await _require_permission(user, "orders.read")
    return _serialise(await _load(db, order_id))


@router.post("/{order_id}/items", response_model=PosOrderResponse)
async def add_item(
    order_id: uuid.UUID,
    data: AddItemRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    order = await _load(db, order_id)
    if data.unit_price is not None:
        await _require_permission(user, "pos.products.open_price")
    await pos_order_service.add_item(
        db,
        order=order,
        user=user,
        product_id=data.product_id,
        quantity=data.quantity,
        unit_price_override=data.unit_price,
        selected_options=[o.model_dump(mode="json") for o in data.selected_options],
        kitchen_notes=data.kitchen_notes,
    )
    return _serialise(await _load(db, order_id))


@router.post("/{order_id}/items/{item_id}/void", response_model=PosOrderResponse)
async def void_item(
    order_id: uuid.UUID,
    item_id: uuid.UUID,
    data: VoidItemRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    await _require_permission(user, "pos.products.void")
    order = await _load(db, order_id)
    order = await pos_order_service.void_item(
        db, order=order, item_id=item_id, user=user, reason_id=data.reason_id
    )
    return _serialise(order)


@router.post("/{order_id}/items/{item_id}/return", response_model=PosOrderResponse)
async def return_item(
    order_id: uuid.UUID,
    item_id: uuid.UUID,
    data: ReturnItemRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    await _require_permission(user, "pos.orders.return")
    order = await _load(db, order_id)
    order = await pos_order_service.return_item(
        db,
        order=order,
        item_id=item_id,
        quantity=data.quantity,
        user=user,
        reason_id=data.reason_id,
    )
    return _serialise(order)


@router.post("/{order_id}/discounts", response_model=PosOrderResponse)
async def apply_discount(
    order_id: uuid.UUID,
    data: ApplyDiscountRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    await _require_permission(
        user,
        "pos.discounts.open" if data.source == "open" else "pos.discounts.predefined",
    )
    order = await _load(db, order_id)
    order = await pos_order_service.apply_discount(
        db,
        order=order,
        user=user,
        name=data.name,
        is_percentage=data.is_percentage,
        value=data.value,
        source=data.source,
        order_item_id=data.order_item_id,
        reference_id=data.reference_id,
    )
    return _serialise(order)


@router.delete("/{order_id}/discounts/{discount_id}", response_model=PosOrderResponse)
async def remove_discount(
    order_id: uuid.UUID,
    discount_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    await _require_permission(user, "pos.discounts.open")
    order = await _load(db, order_id)
    order = await pos_order_service.remove_discount(
        db, order=order, discount_id=discount_id
    )
    return _serialise(order)


@router.post("/{order_id}/charges", response_model=PosOrderResponse)
async def apply_charge(
    order_id: uuid.UUID,
    data: ApplyChargeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    if data.charge_id is None:
        await _require_permission(user, "pos.charges.open")
    order = await _load(db, order_id)
    order = await pos_order_service.apply_charge(
        db,
        order=order,
        charge_id=data.charge_id,
        name=data.name,
        charge_type=data.type,
        value=data.value,
    )
    return _serialise(order)


@router.post("/{order_id}/send-to-kitchen", response_model=list[KitchenTicketResponse])
async def send_to_kitchen(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    await _require_permission(user, "pos.kitchen.send_before_payment")
    order = await _load(db, order_id)
    tickets = await pos_order_service.send_to_kitchen(db, order=order)
    return [await _serialise_ticket(db, t) for t in tickets]


@router.post("/{order_id}/payments", response_model=PosOrderResponse)
async def record_payment(
    order_id: uuid.UUID,
    data: PaymentRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    await _require_permission(user, "pos.payment.perform")
    order = await _load(db, order_id)
    till = await _resolve_till(db, data.till_id, order)
    await pos_order_service.record_payment(
        db,
        order=order,
        user=user,
        payment_method_id=data.payment_method_id,
        amount=data.amount,
        tendered=data.tendered,
        tips=data.tips,
        till=till,
        is_refund=data.is_refund,
        reference=data.reference,
    )
    return _serialise(await _load(db, order_id))


@router.post("/{order_id}/close", response_model=PosOrderResponse)
async def close_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    await _require_permission(user, "pos.payment.perform")
    order = await _load(db, order_id)
    return _serialise(await pos_order_service.close_order(db, order=order, user=user))


@router.post("/{order_id}/void", response_model=PosOrderResponse)
async def void_order(
    order_id: uuid.UUID,
    data: VoidOrderRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    await _require_permission(user, "pos.orders.void")
    order = await _load(db, order_id)
    return _serialise(
        await pos_order_service.void_order(
            db, order=order, user=user, reason_id=data.reason_id
        )
    )


# ─── Kitchen display ──────────────────────────────────────────────────────────

kitchen_router = APIRouter()


async def _serialise_ticket(
    db: AsyncSession, ticket: KitchenTicket
) -> KitchenTicketResponse:
    payload = KitchenTicketResponse.model_validate(ticket)
    order = await db.get(Order, ticket.order_id)
    if order is not None:
        payload.order_number = order.order_number
        payload.check_number = order.check_number
        payload.order_type = order.order_type
        if order.table_id:
            table = await db.get(PosTable, order.table_id)
            payload.table_name = table.name if table else None
    return payload


@kitchen_router.get("/tickets", response_model=list[KitchenTicketResponse])
async def list_tickets(
    branch_id: uuid.UUID,
    kitchen_flow_id: uuid.UUID | None = None,
    include_completed: bool = False,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """The KDS feed: open tickets for a branch, oldest first so nothing ages out."""
    await _require_permission(user, "dashboard.kitchen")
    stmt = (
        select(KitchenTicket)
        .where(KitchenTicket.branch_id == branch_id)
        .options(selectinload(KitchenTicket.items))
    )
    if kitchen_flow_id:
        stmt = stmt.where(KitchenTicket.kitchen_flow_id == kitchen_flow_id)
    if not include_completed:
        stmt = stmt.where(
            KitchenTicket.status.notin_(
                [
                    KitchenTicketStatusEnum.COMPLETED.value,
                    KitchenTicketStatusEnum.CANCELLED.value,
                ]
            )
        )
    stmt = stmt.order_by(KitchenTicket.sent_at).limit(limit)
    tickets = list((await db.execute(stmt)).scalars().unique().all())
    return [await _serialise_ticket(db, t) for t in tickets]


@kitchen_router.put("/tickets/{ticket_id}/status", response_model=KitchenTicketResponse)
async def update_ticket_status(
    ticket_id: uuid.UUID,
    data: KitchenTicketStatusUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    await _require_permission(user, "dashboard.kitchen")
    ticket = await db.get(KitchenTicket, ticket_id)
    if ticket is None:
        raise NotFoundError("Kitchen ticket not found")

    ticket.status = data.status
    now = utcnow()
    if (
        data.status == KitchenTicketStatusEnum.IN_PROGRESS.value
        and not ticket.started_at
    ):
        ticket.started_at = now
    if data.status in (
        KitchenTicketStatusEnum.COMPLETED.value,
        KitchenTicketStatusEnum.CANCELLED.value,
    ):
        ticket.completed_at = now
        for item in ticket.items:
            item.status = data.status
            item.completed_at = now

    await db.flush()
    await db.refresh(ticket)
    return await _serialise_ticket(db, ticket)


@kitchen_router.post(
    "/tickets/{ticket_id}/reprint", response_model=KitchenTicketResponse
)
async def reprint_ticket(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    await _require_permission(user, "pos.kitchen.reprint")
    ticket = await db.get(KitchenTicket, ticket_id)
    if ticket is None:
        raise NotFoundError("Kitchen ticket not found")
    ticket.reprint_count += 1
    ticket.printed_at = utcnow()
    await db.flush()
    await db.refresh(ticket)
    return await _serialise_ticket(db, ticket)


@kitchen_router.get("/open-checks", response_model=list[PosOrderResponse])
async def open_checks(
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Every check still open at a branch — the POS floor/tab view."""
    await _require_permission(user, "orders.read")
    stmt = (
        select(Order)
        .where(
            Order.is_pos.is_(True),
            Order.branch_id == branch_id,
            Order.pos_status == PosOrderStatusEnum.ACTIVE.value,
        )
        .options(
            selectinload(Order.items),
            selectinload(Order.payments),
            selectinload(Order.order_charges),
            selectinload(Order.order_discounts),
            selectinload(Order.order_taxes),
        )
        .order_by(Order.opened_at)
    )
    orders = list((await db.execute(stmt)).scalars().unique().all())
    return [_serialise(o) for o in orders]


__all__ = ["kitchen_router", "router"]
