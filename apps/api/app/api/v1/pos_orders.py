"""The POS order engine's HTTP surface, plus the kitchen display feed."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_current_active_user, get_db
from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models import (
    Branch,
    KitchenTicket,
    KitchenTicketStatusEnum,
    Order,
    OrderStatusEnum,
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
    SplitOrderRequest,
    SplitOrderResponse,
    JoinOrderRequest,
    ChangeTableRequest,
    AssignDriverRequest,
    ScheduleOrderRequest,
    VoidOrderRequest,
)
from app.services import (
    address_format,
    crud_service,
    email_service,
    option_snapshot,
    order_service,
    pos_order_service,
)

router = APIRouter()


def _serialise(order: Order) -> PosOrderResponse:
    payload = PosOrderResponse.model_validate(order)
    payload.customer_id = order.user_id
    payload.amount_paid = order.amount_paid
    payload.balance_due = order.balance_due
    payload.delivery_address = address_format.one_line(order.shipping_address_snapshot)
    # Written out rather than left to the enum's `str` base, so the register
    # reads `"packed"` and never `"OrderStatusEnum.PACKED"`.
    payload.status = order.status.value if order.status is not None else None
    # Voided lines stay in the database for audit but never render on the check.
    payload.items = [
        OrderItemResponse.model_validate(i) for i in order.items if i.status != "void"
    ]
    # One modifier shape, whichever checkout wrote it. The website and the
    # counter write different keys into the same column, and the register knows
    # only the counter's — so a website order with a flavour on it failed to
    # decode, which takes the entire response with it rather than one line.
    for item, row in zip(payload.items, [i for i in order.items if i.status != "void"]):
        item.selected_options_snapshot = option_snapshot.for_register(
            row.selected_options_snapshot
        )
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
        due_at=data.due_at,
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
    # Every other mutating route on this order gates first; this one only ever
    # checked the open-price case, so any authenticated active user could add
    # priced lines to any check they could name the id of.
    await _require_permission(user, "pos.register.access")
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
        course_id=data.course_id,
        weight=data.weight,
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
    course_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """
    Fire the check to the kitchen.

    Pass `course_id` to fire one course only — starters now, mains when the
    table has finished them. Omit it and everything outstanding goes at once,
    which is what a takeaway wants.
    """
    await _require_permission(user, "pos.kitchen.send_before_payment")
    order = await _load(db, order_id)
    tickets = await pos_order_service.send_to_kitchen(
        db, order=order, course_id=course_id
    )
    return [await _serialise_ticket(db, t) for t in tickets]


@router.post("/{order_id}/payments", response_model=PosOrderResponse)
async def record_payment(
    order_id: uuid.UUID,
    data: PaymentRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """
    Record one tender against an order.

    Send an `Idempotency-Key` and a retry of a payment that already landed
    returns the original rather than taking the money twice — which matters
    here because the register's pay sequence is three calls over a 15-second
    timeout and the cashier cannot tell a lost response from a lost payment.
    """
    await _require_permission(user, "pos.payment.perform")
    if data.is_refund:
        await _require_permission(user, "pos.payment.refund")
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
        idempotency_key=idempotency_key,
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


@router.post("/{order_id}/split", response_model=SplitOrderResponse)
async def split_order(
    order_id: uuid.UUID,
    data: SplitOrderRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Move lines onto a second check so a table can pay separately."""
    await _require_permission(user, "pos.orders.split")
    order = await _load(db, order_id)
    original, split = await pos_order_service.split_order(
        db, order=order, user=user, item_ids=data.item_ids
    )
    return SplitOrderResponse(original=_serialise(original), split=_serialise(split))


@router.post("/{order_id}/join", response_model=PosOrderResponse)
async def join_orders(
    order_id: uuid.UUID,
    data: JoinOrderRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Absorb another open check into this one."""
    await _require_permission(user, "pos.orders.join")
    target = await _load(db, order_id)
    source = await _load(db, data.source_order_id)
    return _serialise(
        await pos_order_service.join_orders(db, target=target, source=source, user=user)
    )


@router.post("/{order_id}/table", response_model=PosOrderResponse)
async def change_table(
    order_id: uuid.UUID,
    data: ChangeTableRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Move an open check to a different table."""
    await _require_permission(user, "pos.tables.change_owner")
    order = await _load(db, order_id)
    return _serialise(
        await pos_order_service.change_table(db, order=order, table_id=data.table_id)
    )


@router.post("/{order_id}/driver", response_model=PosOrderResponse)
async def assign_driver(
    order_id: uuid.UUID,
    data: AssignDriverRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """
    Put a delivery order on a driver, or take it back off one.

    Only delivery orders can be dispatched; assigning a driver to a takeaway
    would put it on a dispatch board it should never appear on.
    """
    await _require_permission(user, "pos.driver.act_as")
    order = await _load(db, order_id)
    if order.order_type != "delivery":
        raise BadRequestError("Only a delivery order can be given to a driver")
    if data.driver_id is not None:
        driver = await db.get(User, data.driver_id)
        if driver is None or not driver.is_driver:
            raise BadRequestError("That user is not a driver")
    order.driver_id = data.driver_id
    await db.flush()
    return _serialise(await _load(db, order_id))


@router.post("/{order_id}/schedule", response_model=PosOrderResponse)
async def schedule_order(
    order_id: uuid.UUID,
    data: ScheduleOrderRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Set when an ahead order is wanted — a cake for 4pm tomorrow."""
    await _require_permission(user, "pos.register.access")
    order = await _load(db, order_id)
    order.due_at = data.due_at
    await db.flush()
    return _serialise(await _load(db, order_id))


@router.get("/dispatch/board", response_model=list[PosOrderResponse])
async def dispatch_board(
    branch_id: uuid.UUID | None = None,
    unassigned_only: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """
    Open delivery orders, oldest first — the driver dispatch queue.

    Declared before nothing else claims `/dispatch`, and ordered by when the
    order was opened because the longest-waiting customer goes out first.
    """
    await _require_permission(user, "orders.read")
    stmt = select(Order).where(
        Order.is_pos.is_(True),
        Order.order_type == "delivery",
        Order.pos_status.in_(sorted(pos_order_service.OPEN_STATUSES)),
    )
    if branch_id:
        stmt = stmt.where(Order.branch_id == branch_id)
    if unassigned_only:
        stmt = stmt.where(Order.driver_id.is_(None))
    stmt = stmt.options(
        selectinload(Order.items),
        selectinload(Order.payments),
        selectinload(Order.order_charges),
        selectinload(Order.order_discounts),
        selectinload(Order.order_taxes),
    ).order_by(Order.opened_at.asc().nullslast())
    orders = list((await db.execute(stmt)).scalars().unique().all())
    return [_serialise(o) for o in orders]


@router.post("/{order_id}/park", response_model=PosOrderResponse)
async def park_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Set a check aside so the till is free for the next customer."""
    await _require_permission(user, "pos.register.access")
    order = await _load(db, order_id)
    return _serialise(await pos_order_service.park_order(db, order=order))


@router.post("/{order_id}/resume", response_model=PosOrderResponse)
async def resume_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Bring a parked check back to the register."""
    await _require_permission(user, "pos.register.access")
    order = await _load(db, order_id)
    return _serialise(await pos_order_service.resume_order(db, order=order))


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


@router.post("/{order_id}/accept", response_model=PosOrderResponse)
async def accept_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """
    Take a waiting online order onto the register.

    This is the moment a cashier has *seen* the order: it moves `pending →
    active`, which is what silences the alert on every device at the branch and
    what makes it an open check like any other. Everything after this point —
    sending to the kitchen, marking it packed, printing — is the ordinary flow.

    Idempotent, because two cashiers will press it at once on a busy counter and
    the second one should not get an error for being slower.
    """
    await _require_permission(user, "pos.register.access")
    order = await pos_order_service.get_order(db, order_id)

    if order.pos_status == PosOrderStatusEnum.ACTIVE.value:
        return _serialise(order)
    if order.pos_status != PosOrderStatusEnum.PENDING.value:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Order is {order.pos_status} and cannot be accepted.",
        )

    order.pos_status = PosOrderStatusEnum.ACTIVE.value
    order.accepted_at = utcnow()
    # Who took it, where a cashier had not already been recorded — a storefront
    # order has no creator until somebody claims it.
    order.creator_id = order.creator_id or user.id
    await db.flush()
    return _serialise(await pos_order_service.get_order(db, order_id))


@router.post("/{order_id}/packed", response_model=PosOrderResponse)
async def mark_packed(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """
    The box is finished. Said by whoever finished it.

    This is the event the whole delivery chain hangs off: `packed` is what
    assigns the order to a batch, books a courier and sends the customer the
    email that says their cake is on its way. Until now the only way to say it
    was `PUT /orders/{order_number}/status`, which is gated on `get_admin_user`
    — and a cashier signed in with a branch PIN is not an admin. So a website
    order accepted on the register sat at `confirmed` until somebody opened the
    admin console on a laptop, and if nobody did, no courier was ever booked.

    Deliberately thin. `order_service.update_status` owns the transition rules,
    the batch assignment and the stock and register side effects, and
    `email_service.notify_status_change` owns which email a status earns. A
    second implementation of any of that here is how the register and the
    console would come to disagree about what "packed" does.

    Idempotent for the same reason `accept` is: two people will press it.
    """
    await _require_permission(user, "pos.register.access")
    order = await _load(db, order_id)

    if order.status == OrderStatusEnum.PACKED:
        return _serialise(order)
    if OrderStatusEnum.PACKED not in order_service.VALID_TRANSITIONS.get(
        order.status, set()
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"An order that is {order.status.value} cannot be marked packed.",
        )

    # By order number, because that is what `update_status` takes — it reloads
    # the row with the load options the mailer needs, which is the difference
    # between an email that sends and a `MissingGreenlet` nobody sees.
    await order_service.update_status(db, order.order_number, OrderStatusEnum.PACKED)

    # Inline rather than in a background task, and awaited: this is the email
    # the customer is waiting for, and a background task can be dropped on a
    # restart. It never raises.
    reloaded = await _load(db, order_id)
    await email_service.notify_order(db, reloaded)
    return _serialise(reloaded)
