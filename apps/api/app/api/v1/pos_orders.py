"""The POS order engine's HTTP surface, plus the kitchen display feed."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_current_active_user, get_db
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.permissions import ensure, require
from app.models import (
    Branch,
    DeliveryMethodEnum,
    KitchenTicket,
    Order,
    OrderSourceEnum,
    OrderStatusEnum,
    PosOrderStatusEnum,
    PosTable,
    StatusSourceEnum,
    Till,
    acting_as,
)
from app.models.base import utcnow
from app.models.user import User
from app.schemas.courier import CourierBadge
from app.schemas.pos_order import (
    AddItemRequest,
    ApplyChargeRequest,
    ApplyDiscountRequest,
    AssignDriverRequest,
    ChangeTableRequest,
    JoinOrderRequest,
    KitchenTicketResponse,
    OpenOrderRequest,
    OrderItemResponse,
    PaymentRequest,
    PosOrderResponse,
    ReturnItemRequest,
    ScheduleOrderRequest,
    SplitOrderRequest,
    SplitOrderResponse,
    VoidItemRequest,
    VoidOrderRequest,
)
from app.services import crud_service, email_service, option_snapshot
from app.services.delivery import address_format, driver_proximity
from app.services.orders import order_service
from app.services.pos import pos_order_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _list_load_options():
    """
    Everything `_serialise` reads off a relationship, eager-loaded.

    A named function rather than five lines inside the endpoint so a test can
    build the same query without a database. It is not decoration: a wrong
    attribute name here raises only when SQLAlchemy resolves the option against
    the mapper — at request time, on the endpoint that feeds both the order
    history and the register's incoming-order poll. `selectinload(Order.branch)`
    shipped once with no `branch` relationship on the model and took both out
    with a 500 until it was noticed, which is what
    `test_pos_order_list_query.py` now prevents.
    """
    return (
        selectinload(Order.items),
        selectinload(Order.payments),
        selectinload(Order.order_charges),
        selectinload(Order.order_discounts),
        selectinload(Order.order_taxes),
        # `_serialise` reads the courier and zone off this, and a lazy load
        # inside an async request raises MissingGreenlet rather than quietly
        # issuing a second query.
        selectinload(Order.delivery),
        # The branch's trading hours decide whether a terminal may accept an
        # order by itself.
        selectinload(Order.branch),
    )


def _ordered_page(stmt, *, limit: int, offset: int):
    """
    The register list's stable order plus its paging window.

    A named function, like `_list_load_options`, so the offset guarantee is
    asserted against the endpoint's *own* construct rather than a copy (see
    `test_pos_order_list_query.py`). `Order.id` is the final sort key on purpose:
    `opened_at` and `created_at` can tie — a burst of website orders lands in the
    same instant — and without a unique tiebreaker the same row can straddle a
    page boundary, so the infinite-scroll register would skip or duplicate it as
    the cashier pages through a busy day.
    """
    return (
        stmt.order_by(
            Order.opened_at.desc().nullslast(),
            Order.created_at.desc(),
            Order.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )


def _serialise(order: Order) -> PosOrderResponse:
    payload = PosOrderResponse.model_validate(order)
    payload.customer_id = order.user_id
    payload.amount_paid = order.amount_paid
    payload.balance_due = order.balance_due
    payload.delivery_address = address_format.one_line(order.shipping_address_snapshot)
    # Flattened off the delivery row for the same reason as the address: the
    # register prints a receipt, not an object graph, and the counter needs the
    # courier and the zone on the paper. `provider` is the live answer rather
    # than `original_provider` — the receipt has to name whoever is actually
    # coming, not who the map said would come at checkout.
    #
    # Guarded on the relationship actually being loaded. Every query that feeds
    # this eager-loads it, but `_serialise` is called from two dozen places and
    # a future one that forgets would otherwise raise MissingGreenlet at
    # attribute access — turning a missing courier name into a 500 on the
    # receipt path. Nulls are the right degradation here; a receipt without the
    # zone still prints.
    if "delivery" not in inspect(order).unloaded and order.delivery is not None:
        payload.delivery_provider = order.delivery.provider
        payload.delivery_zone_name = order.delivery.zone_name
        payload.courier_reference = order.delivery.courier_reference
        payload.driver_name = order.delivery.driver_name
        payload.driver_phone = order.delivery.driver_phone
        payload.driver_assignment_count = order.delivery.driver_assignment_count
        payload.driver_location_at = order.delivery.driver_location_at
        # Computed here rather than on the terminal, and for the same reason the
        # money is: two screens deriving a kilometre from raw coordinates would
        # eventually disagree about the same driver. `to_pickup` declines far
        # more often than it answers — no position, a stale one, or a parcel
        # already collected — and null is the honest reading of every one of
        # those. The branch is guarded the same way the courier is, because a
        # caller that forgot to load it must cost a missing distance and not a
        # 500 on the receipt path.
        if "branch" not in inspect(order).unloaded:
            proximity = driver_proximity.to_pickup(order.delivery, order.branch)
            payload.driver_distance_km = proximity.distance_km if proximity else None
    # An aggregator order has no MM delivery row — the marketplace's own rider
    # carries it — so its driver-facing number is the short pickup code we
    # derived at ingest, not a courier reference. Put it where the ticket
    # already looks (`courier_reference` prints ahead of the long external id),
    # so the driver reads "1445", not the 16-digit marketplace order id.
    if order.source == "aggregator" and order.aggregator_display_code:
        payload.courier_reference = order.aggregator_display_code
    # The aggregator's rider, onto the same driver fields the packed screen
    # already renders for an MM courier — so the board card shows a name and a
    # number whatever carries the order. Null until the marketplace assigns one,
    # and there is no live GPS in the GrubOps payload, so no distance follows.
    if order.source == "aggregator":
        payload.driver_name = order.aggregator_driver_name
        payload.driver_phone = order.aggregator_driver_phone
    # Who is carrying it, for the board and cards. The marketplace for an
    # aggregator order; the dispatched courier for a website one.
    payload.courier = CourierBadge.for_order(
        source=order.source,
        aggregator_channel=order.aggregator_channel,
        delivery_provider=payload.delivery_provider,
    )
    # Same guard, same reason. A hint for the terminal, not the enforcement —
    # `accept_order` asks the question again with the branch definitely loaded,
    # so a payload that could not resolve the hours costs a 409 and an alarm
    # rather than a driver at a shut shop.
    if "branch" not in inspect(order).unloaded:
        payload.may_auto_accept = pos_order_service.may_auto_accept(order, order.branch)
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
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("orders.read")),
):
    # An unpaid storefront order is not on this list. See
    # `pos_order_service.paid_for_clause` — the counter is told about a website
    # order when the money lands, not when the checkout page was opened.
    stmt = select(Order).where(
        Order.is_pos.is_(True), pos_order_service.paid_for_clause()
    )
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
    stmt = _ordered_page(
        stmt.options(*_list_load_options()), limit=limit, offset=offset
    )
    orders = list((await db.execute(stmt)).scalars().unique().all())
    return [_serialise(o) for o in orders]


@router.post("", response_model=PosOrderResponse, status_code=status.HTTP_201_CREATED)
async def open_order(
    data: OpenOrderRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("pos.register.access")),
):
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
    _: User = Depends(require("orders.read")),
):
    return _serialise(await _load(db, order_id))


@router.post("/{order_id}/items", response_model=PosOrderResponse)
async def add_item(
    order_id: uuid.UUID,
    data: AddItemRequest,
    db: AsyncSession = Depends(get_db),
    # This route is why the gate is a declared dependency and not a first
    # statement somebody remembers to write: it only ever checked the
    # open-price case, so any authenticated active user could add priced lines
    # to any check they could name the id of.
    user: User = Depends(require("pos.register.access")),
):
    order = await _load(db, order_id)
    if data.unit_price is not None:
        # Imperative because it is conditional: whether a *second* permission
        # applies depends on the body, which no static dependency can read.
        ensure(user, "pos.products.open_price")
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
    user: User = Depends(require("pos.orders.void")),
):
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
    user: User = Depends(require("pos.orders.return")),
):
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
    # Imperative rather than a `require(...)` dependency: which permission this
    # takes depends on the body — an open discount is typed in, a predefined
    # one is picked from a list — and a static dependency cannot read the body.
    ensure(
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
    _: User = Depends(require("pos.discounts.open")),
):
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
    # Imperative because it is conditional: a predefined charge takes no
    # permission at all, and only the body says which kind this is.
    if data.charge_id is None:
        ensure(user, "pos.charges.open")
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
    _: User = Depends(require("pos.kitchen.manage")),
):
    """
    Fire the check to the kitchen.

    Pass `course_id` to fire one course only — starters now, mains when the
    table has finished them. Omit it and everything outstanding goes at once,
    which is what a takeaway wants.
    """
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
    user: User = Depends(require("pos.payment.perform")),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """
    Record one tender against an order.

    Send an `Idempotency-Key` and a retry of a payment that already landed
    returns the original rather than taking the money twice — which matters
    here because the register's pay sequence is three calls over a 15-second
    timeout and the cashier cannot tell a lost response from a lost payment.
    """
    if data.is_refund:
        # Imperative because it is conditional: refunds take a second
        # permission on top of `pos.payment.perform`, and only the body says
        # whether money is going out rather than in.
        ensure(user, "pos.payment.refund")
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
    user: User = Depends(require("pos.payment.perform")),
):
    order = await _load(db, order_id)
    return _serialise(await pos_order_service.close_order(db, order=order, user=user))


@router.post("/{order_id}/void", response_model=PosOrderResponse)
async def void_order(
    order_id: uuid.UUID,
    data: VoidOrderRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("pos.orders.void")),
):
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
    user: User = Depends(require("pos.orders.split_join")),
):
    """Move lines onto a second check so a table can pay separately."""
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
    user: User = Depends(require("pos.orders.split_join")),
):
    """Absorb another open check into this one."""
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
    _: User = Depends(require("pos.orders.edit_others")),
):
    """Move an open check to a different table."""
    order = await _load(db, order_id)
    return _serialise(
        await pos_order_service.change_table(db, order=order, table_id=data.table_id)
    )


@router.post("/{order_id}/driver", response_model=PosOrderResponse)
async def assign_driver(
    order_id: uuid.UUID,
    data: AssignDriverRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("pos.driver.act_as")),
):
    """
    Put a delivery order on a driver, or take it back off one.

    Only delivery orders can be dispatched; assigning a driver to a takeaway
    would put it on a dispatch board it should never appear on.
    """
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
    _: User = Depends(require("pos.register.access")),
):
    """Set when an ahead order is wanted — a cake for 4pm tomorrow."""
    order = await _load(db, order_id)
    order.due_at = data.due_at
    await db.flush()
    return _serialise(await _load(db, order_id))


@router.get("/dispatch/board", response_model=list[PosOrderResponse])
async def dispatch_board(
    branch_id: uuid.UUID | None = None,
    unassigned_only: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("orders.read")),
):
    """
    Open delivery orders, oldest first — the driver dispatch queue.

    Declared before nothing else claims `/dispatch`, and ordered by when the
    order was opened because the longest-waiting customer goes out first.
    """
    stmt = select(Order).where(
        Order.is_pos.is_(True),
        Order.order_type == "delivery",
        Order.pos_status.in_(sorted(pos_order_service.OPEN_STATUSES)),
        pos_order_service.paid_for_clause(),
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
        selectinload(Order.delivery),
    ).order_by(Order.opened_at.asc().nullslast())
    orders = list((await db.execute(stmt)).scalars().unique().all())
    return [_serialise(o) for o in orders]


@router.post("/{order_id}/park", response_model=PosOrderResponse)
async def park_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("pos.register.access")),
):
    """Set a check aside so the till is free for the next customer."""
    order = await _load(db, order_id)
    return _serialise(await pos_order_service.park_order(db, order=order))


@router.post("/{order_id}/resume", response_model=PosOrderResponse)
async def resume_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("pos.register.access")),
):
    """Bring a parked check back to the register."""
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


@kitchen_router.get("/open-checks", response_model=list[PosOrderResponse])
async def open_checks(
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("orders.read")),
):
    """Every check still open at a branch — the POS floor/tab view."""
    stmt = (
        select(Order)
        .where(
            Order.is_pos.is_(True),
            Order.branch_id == branch_id,
            Order.pos_status == PosOrderStatusEnum.ACTIVE.value,
            pos_order_service.paid_for_clause(),
        )
        .options(
            selectinload(Order.items),
            selectinload(Order.payments),
            selectinload(Order.order_charges),
            selectinload(Order.order_discounts),
            selectinload(Order.order_taxes),
            # Eager: `_serialise` reads the courier and zone off this, and a
            # lazy load inside an async request raises MissingGreenlet rather
            # than quietly issuing a second query.
            selectinload(Order.delivery),
        )
        .order_by(Order.opened_at)
    )
    orders = list((await db.execute(stmt)).scalars().unique().all())
    return [_serialise(o) for o in orders]


__all__ = ["kitchen_router", "router"]


@router.post("/{order_id}/accept", response_model=PosOrderResponse)
async def accept_order(
    order_id: uuid.UUID,
    auto: bool = Query(
        False,
        description=(
            "The terminal accepted this by itself rather than a person pressing "
            "Accept. Refused for an order placed outside the branch's trading "
            "hours, which needs a human however the terminal is configured."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("pos.register.access")),
):
    """
    Take a waiting online order onto the register, and call the driver.

    This is the moment a cashier has *seen* the order: it moves `pending →
    active`, which is what silences the alert on every device at the branch and
    what makes it an open check like any other.

    **The courier is no longer booked here.** It was, briefly, and before that
    it happened on packing; both were attempts to answer "when is the shop
    committed to this order" with a register event. `arrived_at_pos` answers it
    directly, and the booking now rides on that: an order is not on this list at
    all until it has arrived, so by the time a cashier can press Accept the van
    is already called and the reference on the ticket already exists.

    What acceptance still is, and all it is: the moment somebody has *seen* the
    order. That silences the alarm on every device at the branch and turns the
    order into an open check. It is not a status, deliberately — it is a fact
    about a person, on a different axis from where the cake is.

    Idempotent, because two cashiers will press it at once on a busy counter and
    the second one should not get an error for being slower.
    """
    order = await pos_order_service.get_order(db, order_id)

    if order.pos_status == PosOrderStatusEnum.ACTIVE.value:
        # Somebody got here first — another terminal at this branch, or the same
        # cashier pressing twice. Still a success: refusing would be a lie about
        # an order that is on the register. The flag is what stops the caller
        # printing a second receipt for it.
        payload = _serialise(order)
        payload.already_accepted = True
        return payload
    if order.pos_status != PosOrderStatusEnum.PENDING.value:
        raise ConflictError(f"Order is {order.pos_status} and cannot be accepted.")
    # Accepting is what turns a website order into an open check the kitchen
    # works from, so it must not be reachable for one nobody has paid for. The
    # list no longer offers these, but a device holding a stale row — or one
    # replaying a queued action after the payment failed — still can ask.
    if not pos_order_service.is_paid_for(order):
        raise ConflictError("This order has not been paid for yet.")
    # The terminal's setting is a permission, not an instruction. An order
    # placed while the shop was shut is accepted by a person or not at all —
    # accepting now prints a ticket and calls a driver, and doing both for a
    # 03:00 order sends a van to a dark shutter. Asked here with the branch
    # certainly loaded, rather than trusting the hint in the payload.
    branch = await db.get(Branch, order.branch_id) if order.branch_id else None
    if auto and not pos_order_service.may_auto_accept(order, branch):
        raise ConflictError(
            "This order was placed outside the branch's opening hours and has "
            "to be accepted by a person."
        )

    order.pos_status = PosOrderStatusEnum.ACTIVE.value
    order.accepted_at = utcnow()
    # Who took it, where a cashier had not already been recorded — a storefront
    # order has no creator until somebody claims it.
    order.creator_id = order.creator_id or user.id
    await db.flush()

    return _serialise(await pos_order_service.get_order(db, order_id))


@router.post("/{order_id}/handed-over", response_model=PosOrderResponse)
async def mark_handed_over(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("pos.register.access")),
):
    """
    The box is in a driver's hands. Said by whoever handed it over.

    **For the zones nobody reports back on.** A Lalamove or noon Send order
    reaches `out_for_delivery` on its own, when the courier's webhook says the
    rider collected — nothing at the counter has to say it, and this endpoint is
    not how it gets there. A third-party zone has no integration at all: a van
    somebody already uses turns up, a person hands over a bag, and until now the
    only thing that could record that was an admin on a laptop marking the whole
    order delivered — which skipped the state entirely and told the customer it
    had arrived when it had only left.

    Gated on `packed` by the transition table, which is the honest precondition:
    a box that is not finished cannot be handed to anybody.

    Idempotent, like `accept` and `packed`, because two people will press it.
    """
    order = await _load(db, order_id)

    if order.status == OrderStatusEnum.OUT_FOR_DELIVERY:
        return _serialise(order)
    if OrderStatusEnum.OUT_FOR_DELIVERY not in order_service.VALID_TRANSITIONS.get(
        order.status, set()
    ):
        raise ConflictError(
            f"An order that is {order.status.value} cannot be handed over."
        )

    with acting_as(
        StatusSourceEnum.POS.value,
        actor_id=user.id,
        actor_label=user.email,
        note="handed to the driver at the counter",
    ):
        await order_service.update_status(
            db, order.order_number, OrderStatusEnum.OUT_FOR_DELIVERY
        )

    # The email that says it is on its way, with whatever tracking exists. Same
    # reasoning as `mark_packed`: inline and awaited, because a background task
    # can be dropped on a restart and this is the message the customer is
    # waiting for.
    reloaded = await _load(db, order_id)
    await email_service.notify_order(db, reloaded)
    return _serialise(reloaded)


@router.post("/{order_id}/collected", response_model=PosOrderResponse)
async def mark_collected(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("pos.register.access")),
):
    """
    The customer has taken the order at the counter. Said by whoever handed it over.

    **The pickup counterpart of `handed-over`.** A store-pickup order has no
    driver and no courier telemetry — nothing reports back that the customer
    collected it. Until now the only way to record it was an admin on a laptop
    marking the whole order delivered; the shop that actually handed the box
    over had no button. This is that button.

    `delivered` is the stored status; the storefront and the receipt render it as
    "Collected" because the order is `pickup`. A delivery order is rejected — its
    hand-over is `handed-over` (→ `out_for_delivery`), driven by the courier.

    Deliberately thin, like `mark_packed`/`mark_handed_over`: `order_service`
    owns the transition rules and side effects, `email_service` owns which email
    the status earns. Idempotent, because two people will press it.
    """
    order = await _load(db, order_id)

    method = getattr(order.delivery_method, "value", order.delivery_method)
    if method != DeliveryMethodEnum.PICKUP.value:
        raise BadRequestError("Only a store-pickup order is collected at the counter")

    if order.status == OrderStatusEnum.DELIVERED:
        return _serialise(order)
    if OrderStatusEnum.DELIVERED not in order_service.VALID_TRANSITIONS.get(
        order.status, set()
    ):
        raise ConflictError(
            f"An order that is {order.status.value} cannot be collected."
        )

    with acting_as(
        StatusSourceEnum.POS.value,
        actor_id=user.id,
        actor_label=user.email,
        note="collected at the counter",
    ):
        await order_service.update_status(
            db, order.order_number, OrderStatusEnum.DELIVERED
        )

    # The "Collected" email, inline and awaited for the same reason as
    # `mark_handed_over`: a background task can be dropped on a restart and this
    # is the message the customer is waiting on.
    reloaded = await _load(db, order_id)
    await email_service.notify_order(db, reloaded)
    return _serialise(reloaded)


@router.post("/{order_id}/packed", response_model=PosOrderResponse)
async def mark_packed(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("pos.register.access")),
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
    order = await _load(db, order_id)

    if order.status == OrderStatusEnum.PACKED:
        return _serialise(order)
    if OrderStatusEnum.PACKED not in order_service.VALID_TRANSITIONS.get(
        order.status, set()
    ):
        raise ConflictError(
            f"An order that is {order.status.value} cannot be marked packed."
        )

    # By order number, because that is what `update_status` takes — it reloads
    # the row with the load options the mailer needs, which is the difference
    # between an email that sends and a `MissingGreenlet` nobody sees.
    with acting_as(
        StatusSourceEnum.POS.value,
        actor_id=user.id,
        actor_label=user.email,
    ):
        await order_service.update_status(
            db, order.order_number, OrderStatusEnum.PACKED
        )

    # Inline rather than in a background task, and awaited: this is the email
    # the customer is waiting for, and a background task can be dropped on a
    # restart. It never raises.
    reloaded = await _load(db, order_id)
    await email_service.notify_order(db, reloaded)
    return _serialise(reloaded)


@router.post("/{order_id}/cancel", response_model=PosOrderResponse)
async def cancel_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("pos.orders.void")),
):
    """
    Cancel an aggregator order from the counter — the red button beside Packed.

    **Aggregator only, deliberately.** Cancelling here runs the full
    `cancelled` machinery: it releases the stock, voids the check on the register
    and — the point of doing it from the counter rather than a laptop — declines
    the Foodics order so the marketplace stops the rider. For a *website*
    order that same move would refund the customer's card and cancel a booked MM
    courier, which is an admin decision on the order screen, not a counter
    button; so a stale device asking to cancel one is refused here rather than
    quietly issuing a refund.

    Reachable from `arrived_at_pos` (the map allows it) and from `packed` (it
    does not — `order_service.update_status` widens it for an aggregator order
    via `AGGREGATOR_CANCELLABLE_FROM`; the Foodics decline then applies only while
    the order is still pending, and an already-accepted one is recorded for a
    person to void in the console). `update_status` raises if the order cannot be
    cancelled, which is what a person pressing a button should get.

    Idempotent on an already-cancelled order, like `accept` and `packed`.
    """
    order = await _load(db, order_id)

    if order.source != OrderSourceEnum.AGGREGATOR.value:
        raise ConflictError(
            "Only an aggregator order can be cancelled from the register."
        )
    if order.status == OrderStatusEnum.CANCELLED:
        return _serialise(order)

    with acting_as(
        StatusSourceEnum.POS.value,
        actor_id=user.id,
        actor_label=user.email,
        note="cancelled at the counter",
    ):
        await order_service.update_status(
            db, order.order_number, OrderStatusEnum.CANCELLED
        )

    return _serialise(await _load(db, order_id))
