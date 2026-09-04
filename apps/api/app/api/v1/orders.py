from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import (
    get_current_active_user,
    get_db,
    get_optional_user,
)
from app.core.exceptions import (
    BadGatewayError,
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from app.core.limiter import limiter
from app.core.permissions import require
from app.models.aggregator import AggregatorOrder, AggregatorOrderStatusEvent
from app.models.branch import Branch
from app.models.delivery_polygon import FulfilmentProviderEnum
from app.models.order import Order, OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.models.order_driver import OrderDriver
from app.models.order_status_event import OrderStatusEvent, StatusSourceEnum, acting_as
from app.models.user import User
from app.schemas.courier import CourierBadge
from app.schemas.fulfilment import FulfilmentResponse
from app.schemas.order import (
    OrderAdminDetails,
    OrderBranchSummary,
    OrderCreate,
    OrderEconomicsResponse,
    OrderListResponse,
    OrderResponse,
    OrderStatusUpdate,
    OrderTimelineEntry,
)
from app.schemas.order_preview import OrderPreviewRequest, OrderPreviewResponse
from app.services import audit_service, email_service
from app.services.couriers import (
    courier_service,
    lalamove_service,
    noon_send_service,
    slider_service,
)
from app.services.delivery import (
    driver_proximity,
    fulfilment_reassignment,
    fulfilment_service,
)
from app.services.orders import order_economics, order_service

router = APIRouter()


class PaginatedOrders(BaseModel):
    items: list[OrderListResponse]
    total: int
    page: int
    per_page: int
    pages: int


class PreviousDriver(BaseModel):
    """A driver who used to be on this order, and when they stopped being."""

    sequence: int
    name: str | None = None
    phone: str | None = None
    plate: str | None = None
    assigned_at: datetime | None = None
    replaced_at: datetime | None = None


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
    #: The seven digits a driver quotes. Null on a third-party zone, and on
    #: bookings made before the reference existed.
    courier_reference: str | None
    courier_order_id: str | None
    courier_status: str | None
    share_link: str | None
    driver_name: str | None
    driver_phone: str | None
    driver_plate: str | None
    #: When this driver took the order, and which driver they are — 1 for the
    #: first, 2 for whoever took over. Both are here because "the driver
    #: changed" is the thing the card could not say: a swapped booking used to
    #: render exactly like an unswapped one, and the person on the phone to the
    #: courier had no way to know they were describing somebody else.
    driver_assigned_at: datetime | None = None
    driver_assignment_count: int = 0
    #: Everyone who has held this order, oldest stint first — empty on the
    #: overwhelming majority, which are carried by the one driver they started
    #: with. From `order_drivers`, where exactly one row is active by database
    #: constraint rather than by convention.
    previous_drivers: list["PreviousDriver"] = []
    #: How far the driver still is from the branch, and when the position behind
    #: that was true. Null unless a named driver is on the way *to the kitchen* —
    #: after collection the number would only grow — and null rather than stale
    #: when the courier has gone quiet.
    #:
    #: Driven road kilometres where Mapbox routed the leg recently, straight line
    #: times the fitted detour factor otherwise. `driver_eta_minutes` is how to
    #: tell which: it is null on the fallback, because a duration derived by
    #: dividing an estimate by an assumed speed is a guess wearing the clothes of
    #: a measurement.
    driver_distance_km: float | None = None
    driver_eta_minutes: float | None = None
    driver_location_at: datetime | None = None
    pod_status: str | None
    pod_image_url: str | None
    booked_at: datetime | None
    #: When the parcel was collected and when it arrived. From the order's own
    #: history rather than from `order_deliveries`, which used to hold its own
    #: copy of both — see the timeline comment on the model.
    picked_up_at: datetime | None
    delivered_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    last_error: str | None
    needs_attention: bool

    #: Whether an admin moved this order off the courier its zone chose, and
    #: who that was. Null on the overwhelming majority.
    original_provider: str | None = None

    #: The carrier as a badge (code, name, logo) so the fulfilment panel can show
    #: a logo beside the provider name. Built from `provider`.
    courier: CourierBadge | None = None

    @classmethod
    def of(
        cls,
        d: OrderDelivery,
        reached: dict[str, datetime] | None = None,
        *,
        branch: Branch | None = None,
        drivers: list[OrderDriver] | None = None,
    ) -> "OrderDeliveryResponse":
        """*reached* is `fulfilment_service.reached_at` — when the order hit each
        status. Optional so a caller that only wants the courier and the cost
        need not pay for the query; the two timestamps are then null.

        *branch* and *drivers* are optional for the same reason, and their
        absence degrades to a null distance and an empty history rather than to
        an exception — a list endpoint that does not load them is showing less,
        not failing."""
        reached = reached or {}
        proximity = driver_proximity.to_pickup(d, branch)
        cost = d.cost_total if d.cost_total is not None else d.quoted_cost
        margin = (
            float(d.fee_charged) - float(cost)
            if d.fee_charged is not None and cost is not None
            else None
        )
        return cls(
            provider=d.provider,
            courier=CourierBadge.for_order(
                source="online", delivery_provider=d.provider
            ),
            zone_name=d.zone_name,
            fee_charged=float(d.fee_charged) if d.fee_charged is not None else None,
            quoted_cost=float(d.quoted_cost) if d.quoted_cost is not None else None,
            quoted_currency=d.quoted_currency,
            quoted_distance_m=d.quoted_distance_m,
            cost_total=float(d.cost_total) if d.cost_total is not None else None,
            margin=margin,
            courier_reference=d.courier_reference,
            courier_order_id=d.courier_order_id,
            courier_status=d.courier_status,
            share_link=d.share_link,
            driver_name=d.driver_name,
            driver_phone=d.driver_phone,
            driver_plate=d.driver_plate,
            driver_assigned_at=d.driver_assigned_at,
            driver_assignment_count=d.driver_assignment_count,
            previous_drivers=[
                PreviousDriver(
                    sequence=row.sequence,
                    name=row.name,
                    phone=row.phone,
                    plate=row.plate,
                    assigned_at=row.assigned_at,
                    replaced_at=row.replaced_at,
                )
                for row in (drivers or [])
                if not row.is_active
            ],
            driver_distance_km=proximity.distance_km if proximity else None,
            driver_eta_minutes=proximity.minutes if proximity else None,
            driver_location_at=proximity.at if proximity else None,
            pod_status=d.pod_status,
            pod_image_url=d.pod_image_url,
            booked_at=d.booked_at,
            picked_up_at=reached.get(OrderStatusEnum.OUT_FOR_DELIVERY.value),
            delivered_at=reached.get(OrderStatusEnum.DELIVERED.value),
            cancelled_at=d.cancelled_at,
            cancel_reason=d.cancel_reason,
            last_error=d.last_error,
            needs_attention=d.needs_attention,
            original_provider=d.original_provider,
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
    # No `cache_delete_pattern("analytics:*")` here any more, deliberately.
    #
    # It used to run on this one line and nowhere else, which made the console's
    # figures look like they were kept fresh when they were not: a payment
    # webhook confirming an order, an admin moving one, a refund and every
    # counter sale all move the same numbers and busted nothing. So the
    # dashboard was current only for the one event out of five that happened to
    # pass through this handler — and a reader who knew about this line would
    # trust the other four.
    #
    # Analytics is TTL-only, by design: `_ANALYTICS_TTL` is five minutes, which
    # is the right answer for margin figures nobody reads to the second, and it
    # is the same answer for every writer. Adding a bust to the other four
    # paths would mean a Redis keyspace scan on every sale at the till to save
    # a dashboard five minutes.
    return order


@router.post("/preview", response_model=OrderPreviewResponse)
async def preview_order(
    data: OrderPreviewRequest,
    x_session_id: str | None = Header(None, alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
):
    """
    The exact totals an order placed right now would be written with.

    The checkout renders this and computes nothing. It used to derive the grand
    total twice in one file from different inputs, mirror the small-basket fee
    rule in TypeScript, and print a VAT line from a formula that ignored both
    fees — four numbers a browser had to keep in step with a server it could not
    see, on the one screen where a disagreement is money.

    Same inputs as `POST /orders`, minus everything that is about writing a row.
    Never refuses for a state the customer can still fix: an unserviceable pin,
    a coupon that stopped applying and a basket still loading are all answers
    here, and refusals belong on the pay button.

    Public, like the quote it replaces: a guest is priced from the session
    basket and a signed-in customer from theirs.
    """
    return await order_service.preview_order(
        db,
        data,
        current_user.id if current_user else None,
        fallback_email=current_user.email if current_user else None,
        session_id=None if current_user else x_session_id,
    )


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
        pattern="^(online|counter|aggregator)$",
        description="`online` for the storefront, `counter` for the till, "
        "`aggregator` for a marketplace order. Omit for all — they are one "
        "ledger.",
    ),
    courier: str | None = Query(
        None,
        description="Narrow to one carrier by its code — a marketplace channel "
        "(`talabat`, `keeta`, `noon_food`, `deliveroo`, `careem`), a dispatch "
        "provider (`lalamove`, `noon_send`, `slider`, `third_party`), or "
        "`counter`.",
    ),
    couriers: list[str] | None = Query(
        None, description="Multi-select carrier codes; the OR of them (see `courier`)."
    ),
    statuses: list[str] | None = Query(
        None, description="Multi-select order statuses; the OR of them."
    ),
    date_from: str | None = Query(
        None, description="ISO date; with date_to, an inclusive day range."
    ),
    date_to: str | None = Query(
        None, description="ISO date; with date_from, an inclusive day range."
    ),
    branch_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("orders.read")),
):
    """Every order, from any channel (admin only). Filters mirror the dashboard."""
    items, total = await order_service.get_all_admin(
        db,
        status=status,
        search=search,
        page=page,
        per_page=per_page,
        channel=channel,
        courier=courier,
        branch_id=branch_id,
        statuses=statuses,
        couriers=couriers,
        date_from=date_from,
        date_to=date_to,
    )
    pages = max(1, (total + per_page - 1) // per_page)
    return PaginatedOrders(
        items=items, total=total, page=page, per_page=per_page, pages=pages
    )


#: Orders that are not going anywhere, whatever their delivery row still says.
#:
#: `cancelled` is the ending; `refunded` and `disputed` are what follows it.
#: None of them may call a driver or ask a courier for an update, and the point
#: of refusing here rather than only hiding the buttons is that hiding a control
#: is not enforcement: the admin screen is one client, and a stale tab holding
#: yesterday's order is a perfectly ordinary way to press it anyway.
#:
#: `undelivered` was here and is not any more. A failed handover is a cake on a
#: shelf that did not get in today, and the shop can try again — by hand, with a
#: different courier, or with the same one tomorrow. What stops *that* order is
#: the refund guard below, not the status.
_SETTLED_STATUSES = {
    OrderStatusEnum.CANCELLED,
    OrderStatusEnum.REFUNDED,
    OrderStatusEnum.DISPUTED,
}


def _assert_still_going_somewhere(order: Order, verb: str) -> None:
    """
    Refuse anything that would spend a courier on an order that is over.

    Two questions, and the second is the one that carries the history. Orders
    that reached `undelivered` before it stopped being an ending were refunded
    automatically on the way in — the money is at a bank, and no column we could
    write now would take it back. Re-dispatching one would be delivering a cake
    the customer has already been paid for.

    Asked of `refunded_amount` rather than of the status, so it also covers an
    order refunded by hand in a gateway dashboard, and so it needs no backfill
    to be true of every row already in the table.
    """
    if order.status in _SETTLED_STATUSES:
        raise ConflictError(
            f"An order that is {order.status.value} cannot be {verb} — it is "
            "not going anywhere. Refund it if the customer is owed money."
        )
    if (order.refunded_amount or 0) > 0:
        raise ConflictError(
            f"An order that has been refunded cannot be {verb} — "
            f"{order.refunded_amount} has already gone back to the customer."
        )


@router.post("/{order_number}/delivery/refresh", response_model=OrderDeliveryResponse)
async def refresh_order_delivery(
    order_number: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("orders.manage")),
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
        raise NotFoundError(f"Order '{order_number}' not found")
    _assert_still_going_somewhere(order, "checked")

    delivery = await lalamove_service.get_delivery(db, order.id)
    if delivery is None:
        raise NotFoundError(f"No delivery recorded for order '{order_number}'")
    # The couriers whose statuses only ever reach us by push, and who do not
    # retry one that is lost. Lalamove is not among them — they push their own
    # updates and retry for a day, so a button here would be a 400 waiting to
    # happen and a needless call besides.
    refreshers = {
        FulfilmentProviderEnum.NOON_SEND.value: noon_send_service.refresh,
        # Every Slider tier reads back the same way — bike and car are one
        # courier — so all three map to the one refresh.
        FulfilmentProviderEnum.SLIDER.value: slider_service.refresh,
        FulfilmentProviderEnum.SLIDER_BIKE.value: slider_service.refresh,
        FulfilmentProviderEnum.SLIDER_CAR.value: slider_service.refresh,
    }
    ask = refreshers.get(delivery.provider)
    if ask is None:
        raise BadRequestError(
            f"{delivery.provider} does not need asking — Lalamove pushes its "
            "own updates and retries them for a day, and a third party has no "
            "status to read."
        )

    return await _delivery_payload(db, order, await ask(db, order.id) or delivery)


class TrackOrderRequest(BaseModel):
    order_number: str
    email: str


class TrackOrderResponse(BaseModel):
    """
    What a guest sees on the track page.

    Everything here is also on `OrderResponse`, and it stops well short of it:
    the tracking page is reached with an order number and an email, which two
    people can plausibly both hold, so it shows where the order is rather than
    what was bought, what it cost or where it is going. What it *did* lack was
    the part that makes the page worth loading twice — when the order arrives,
    where to collect it, and whether there is a rider to watch — and that is
    the same `fulfilment` block the account page and the emails read.
    """

    order_number: str
    status: str
    delivery_method: str
    items_count: int
    created_at: str
    total: float
    fulfilment: FulfilmentResponse | None = None


@router.post("/track", response_model=TrackOrderResponse)
@limiter.limit("15/minute")
async def track_order(
    request: Request,
    data: TrackOrderRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Public endpoint to look up order status by order number + email.

    Rate limited: order numbers are a date plus a counter, so unbounded
    attempts here are a way to pair a guessed number with a guessed address.
    """
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
        raise NotFoundError("Order not found. Check your order number and email.")
    return TrackOrderResponse(
        order_number=order.order_number,
        status=order.status.value,
        delivery_method=order.delivery_method.value,
        items_count=sum(item.quantity for item in order.items),
        created_at=order.created_at.strftime("%Y-%m-%d"),
        total=float(order.total),
        fulfilment=FulfilmentResponse.of(await fulfilment_service.for_order(db, order)),
    )


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
    admin: User = Depends(require("orders.manage")),
):
    """Update order status with validated transitions (admin only). Triggers email notification."""
    old_order = await order_service.get_by_order_number(db, order_number, admin=True)
    old_status = old_order.status.value

    with acting_as(
        StatusSourceEnum.ADMIN.value,
        actor_id=admin.id,
        actor_label=admin.email,
        note=data.admin_notes,
    ):
        order = await order_service.update_status(
            db, order_number, data.status, data.admin_notes
        )

    # Send email inline (never raises — safe to await before response).
    # Background tasks can be silently dropped on Cloud Run / serverless.
    #
    # Which email a status earns is `email_service.notify_status_change`'s
    # decision, not this endpoint's — the couriers move orders too, and a
    # per-caller `if` chain is how "delivered" ended up notifying nobody.
    await email_service.notify_status_change(order)
    if data.status == OrderStatusEnum.CONFIRMED:
        await email_service.send_owner_order_notification(order)

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
        raise NotFoundError(f"No delivery recorded for order '{order_number}'")
    return delivery


@router.get("/{order_number}/economics", response_model=OrderEconomicsResponse)
async def get_order_economics(
    order_number: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("orders.read")),
):
    """
    What the shop kept on this order.

    Admin-only, and its own endpoint rather than fields on the order: what a
    courier cost and what a processor keeps are not the customer's business, and
    a separate route is what keeps that a property of the API rather than a rule
    somebody has to remember when adding a field.
    """
    order = await _load_order(db, order_number)
    result = await order_economics.for_order(db, order)
    return OrderEconomicsResponse(
        charged=float(result.charged),
        items_value=float(result.items_value),
        courier_cost=(
            float(result.courier_cost) if result.courier_cost is not None else None
        ),
        aggregator_fee=(
            float(result.aggregator_fee) if result.aggregator_fee is not None else None
        ),
        processing_fee=float(result.processing_fee),
        processing_fee_is_estimated=result.processing_fee_is_estimated,
        refunded=float(result.refunded),
        cancellation_fee=(
            float(result.cancellation_fee)
            if result.cancellation_fee is not None
            else None
        ),
        net=float(result.net),
        margin_on_charged=(
            float(result.margin_on_charged)
            if result.margin_on_charged is not None
            else None
        ),
        margin_on_items=(
            float(result.margin_on_items)
            if result.margin_on_items is not None
            else None
        ),
        items_before_discount=float(result.items_before_discount),
        cost_cover=(
            float(result.cost_cover) if result.cost_cover is not None else None
        ),
        covers_direct_cost=result.covers_direct_cost,
        direct_cost_threshold=float(order_economics.DIRECT_COST_THRESHOLD),
    )


@router.get("/{order_number}/delivery", response_model=OrderDeliveryResponse)
async def get_order_delivery(
    order_number: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("orders.read")),
):
    """Who is carrying this order, where it is, and what it cost us."""
    order = await _load_order(db, order_number)
    return await _delivery_payload(db, order, await _load_delivery(db, order_number))


@router.post("/{order_number}/delivery/dispatch", response_model=OrderDeliveryResponse)
async def dispatch_order_delivery(
    order_number: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("orders.manage")),
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
        raise NotFoundError(f"Order '{order_number}' not found")
    _assert_still_going_somewhere(order, "dispatched")

    delivery = await courier_service.dispatch(db, order)
    if delivery is None:
        raise NotFoundError(f"No delivery recorded for order '{order_number}'")

    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="order_delivery",
        entity_id=order_number,
        entity_label=order_number,
        admin=admin,
        changes={
            "courier_reference": delivery.courier_reference,
            "courier_order_id": delivery.courier_order_id,
            "error": delivery.last_error,
        },
        request=request,
    )
    return await _delivery_payload(db, order, delivery)


# ── moving a third-party order onto Lalamove (admin only) ─────────────────────


class LalamoveQuoteResponse(BaseModel):
    """What it would cost us to put this order on a courier we book.

    Everything an admin needs to decide, and the reason it is two endpoints:
    they are agreeing to `cost`, and `assign` will only book at the
    `quotation_id` that produced it.
    """

    quotation_id: str
    cost: float
    currency: str
    distance_m: int | None
    #: Five minutes from now, in practice. Shown so the dialog can say the price
    #: has gone stale rather than only finding out when the booking is refused.
    expires_at: datetime | None
    #: What the customer actually paid for delivery. The quote does not change
    #: it — see `assign_order_to_lalamove`.
    fee_charged: float | None
    #: `fee_charged - cost`. Negative means this delivery loses money, which is
    #: a decision for the person pressing the button rather than a reason to
    #: refuse.
    margin: float | None


async def _delivery_payload(
    db: AsyncSession, order: Order, delivery: OrderDelivery
) -> OrderDeliveryResponse:
    """
    The delivery card, with everything it needs to be complete.

    One function rather than four call sites each remembering to fetch the
    branch and the driver ledger — which is how three of them would end up
    showing no distance and no history, and nobody would notice because a null
    renders as an em dash.
    """
    branch = await db.get(Branch, order.branch_id) if order.branch_id else None
    drivers = list(
        (
            await db.execute(
                select(OrderDriver)
                .where(OrderDriver.order_id == order.id)
                .order_by(OrderDriver.sequence)
            )
        )
        .scalars()
        .all()
    )
    return OrderDeliveryResponse.of(
        delivery,
        await fulfilment_service.reached_at(db, order),
        branch=branch,
        drivers=drivers,
    )


async def _load_order(db: AsyncSession, order_number: str) -> Order:
    order = (
        (await db.execute(select(Order).where(Order.order_number == order_number)))
        .scalars()
        .first()
    )
    if order is None:
        raise NotFoundError(f"Order '{order_number}' not found")
    return order


def _quote_response(
    quote: lalamove_service.ReassignQuote, delivery: OrderDelivery
) -> LalamoveQuoteResponse:
    fee = float(delivery.fee_charged) if delivery.fee_charged is not None else None
    cost = float(quote.estimate.cost)
    return LalamoveQuoteResponse(
        quotation_id=quote.estimate.quotation_id or "",
        cost=cost,
        currency=quote.estimate.currency,
        distance_m=quote.estimate.distance_m,
        expires_at=quote.expires_at,
        fee_charged=fee,
        margin=None if fee is None else fee - cost,
    )


def _assert_assignable(order: Order, delivery: OrderDelivery) -> None:
    """
    Whether this order may be handed to Lalamove, for the superseded endpoints.

    Kept only so `/delivery/lalamove/quote` and `/delivery/lalamove/assign` keep
    behaving exactly as they did while an admin tab open on the old dialog is
    still out there. Everything new goes through `fulfilment_reassignment`,
    which asks a broader and better-argued set of questions.
    """
    if delivery.provider != FulfilmentProviderEnum.THIRD_PARTY.value:
        raise ConflictError(f"This order is already carried by {delivery.provider}.")


class FulfilmentTargetResponse(BaseModel):
    """One courier this order could be moved to."""

    provider: str
    available: bool
    #: Why not, in words for the admin. None when available.
    reason: str | None = None


class CancellationExposureResponse(BaseModel):
    """Whether calling off the current booking is likely to cost anything.

    Carries no figure. Neither courier quotes a cancellation fee over an API —
    see `fulfilment_reassignment.Exposure`.
    """

    will_be_charged: bool
    reason: str


class FulfilmentOptionsResponse(BaseModel):
    """Where this order may go, and what stands in the way. **Admin only.**"""

    current: str
    #: What the zone chose. Equal to `current` on most orders; the difference is
    #: the interesting part.
    preferred: str
    targets: list[FulfilmentTargetResponse]
    blocked: str | None = None
    must_abandon_first: bool = False
    exposure: CancellationExposureResponse | None = None


class FulfilmentQuoteResponse(BaseModel):
    """What moving this order to one courier would cost **us**.

    The customer's fee never changes — see `reassign_order_fulfilment`. `cost`
    is ours, and `margin` is what we would keep of what they already paid.
    """

    provider: str
    #: None for a third party, whom we neither book nor price.
    cost: float | None
    currency: str | None
    distance_m: int | None
    #: Lalamove issue one and will only book against it, so it travels back to
    #: the confirm. noon Send price from a rate card, so there is nothing to pin
    #: and this is null — a client must not treat that as an error.
    quotation_id: str | None
    expires_at: datetime | None
    fee_charged: float | None
    margin: float | None
    #: The booking this move would call off, if there is one.
    cancels_booking: str | None = None


def _options_response(
    options: fulfilment_reassignment.Options,
) -> FulfilmentOptionsResponse:
    return FulfilmentOptionsResponse(
        current=options.current,
        preferred=options.preferred,
        targets=[
            FulfilmentTargetResponse(
                provider=t.provider, available=t.available, reason=t.reason
            )
            for t in options.targets
        ],
        blocked=options.blocked,
        must_abandon_first=options.must_abandon_first,
        exposure=(
            None
            if options.exposure is None
            else CancellationExposureResponse(
                will_be_charged=options.exposure.will_be_charged,
                reason=options.exposure.reason,
            )
        ),
    )


def _fulfilment_quote_response(
    quote: fulfilment_reassignment.Quote,
) -> FulfilmentQuoteResponse:
    return FulfilmentQuoteResponse(
        provider=quote.provider,
        cost=None if quote.cost is None else float(quote.cost),
        currency=quote.currency,
        distance_m=quote.distance_m,
        quotation_id=quote.quotation_id,
        expires_at=quote.expires_at,
        fee_charged=(None if quote.fee_charged is None else float(quote.fee_charged)),
        margin=None if quote.margin is None else float(quote.margin),
        cancels_booking=quote.cancels_booking,
    )


@router.get(
    "/{order_number}/delivery/fulfilment-options",
    response_model=FulfilmentOptionsResponse,
)
async def order_fulfilment_options(
    order_number: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("orders.manage")),
):
    """
    Which couriers this order may be moved to, and why the others are refused.

    Reads and writes nothing. Every refusal comes back as a sentence rather than
    a missing option, because a greyed-out button that will not say why is how
    somebody ends up ringing a courier to ask a question the screen already knew
    the answer to.
    """
    order = await _load_order(db, order_number)
    delivery = await _load_delivery(db, order_number)
    return _options_response(
        await fulfilment_reassignment.options_for(db, order, delivery)
    )


class FulfilmentQuoteRequest(BaseModel):
    provider: str


@router.post(
    "/{order_number}/delivery/fulfilment-quote",
    response_model=FulfilmentQuoteResponse,
)
async def quote_order_fulfilment(
    order_number: str,
    data: FulfilmentQuoteRequest,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("orders.manage")),
):
    """
    What moving this order to one courier would cost us.

    Spends nothing and writes nothing — it exists so a person sees the number
    before any money does. A Lalamove quotation is valid for five minutes and
    the confirm books at that exact one; a noon Send figure is a rate card and
    cannot go stale.
    """
    order = await _load_order(db, order_number)
    quote, error = await fulfilment_reassignment.quote(db, order, target=data.provider)
    if quote is None:
        # 502 rather than 500: this is a courier declining or unreachable, and
        # the message is written for the admin reading it.
        raise BadGatewayError(error or "No price available")
    return _fulfilment_quote_response(quote)


class FulfilmentReassignRequest(BaseModel):
    provider: str
    #: The id from the quote just agreed to. Required for Lalamove, who will
    #: only book at a price a person actually saw. Ignored for noon Send and a
    #: third party, neither of whom issues one.
    quotation_id: str | None = None


@router.post("/{order_number}/delivery/reassign", response_model=OrderDeliveryResponse)
async def reassign_order_fulfilment(
    order_number: str,
    data: FulfilmentReassignRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("orders.manage")),
):
    """
    Move this order to a different courier.

    **The customer's delivery fee does not change.** They paid their zone's
    published fee and that is what they paid; the quote is our cost. The gap
    shows as `margin`, and it is the only number that moves — which makes it the
    thing the person pressing the button is actually agreeing to.

    Where the order may go is the map's business, not this endpoint's: each zone
    names a preferred courier and the alternates its orders may be moved to, so
    a Dubai order cannot be handed to a fleet that cannot reach Dubai. See
    `fulfilment_reassignment`.

    A lapsed Lalamove quotation is a **409 carrying the new price**, not a quiet
    re-book at a figure nobody agreed to.
    """
    order = await _load_order(db, order_number)
    before = (await _load_delivery(db, order_number)).provider

    delivery = await fulfilment_reassignment.move(
        db, order, target=data.provider, quotation_id=data.quotation_id
    )

    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="order_delivery",
        entity_id=order_number,
        entity_label=order_number,
        admin=admin,
        changes={
            "provider": {"from": before, "to": delivery.provider},
            "quoted_cost": (
                float(delivery.quoted_cost)
                if delivery.quoted_cost is not None
                else None
            ),
            "courier_reference": delivery.courier_reference,
            "courier_order_id": delivery.courier_order_id,
        },
        request=request,
    )
    return await _delivery_payload(db, order, delivery)


class AbandonBookingRequest(BaseModel):
    #: Set by a client that has shown the operator `exposure.reason`. Checked
    #: server-side rather than trusted to a dialog: this is the one action here
    #: that can cost money, and "the UI asked" is not a record that anybody was
    #: told.
    acknowledged_charge: bool = False


@router.post(
    "/{order_number}/delivery/abandon-booking",
    response_model=OrderDeliveryResponse,
)
async def abandon_order_booking(
    order_number: str,
    data: AbandonBookingRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("orders.manage")),
):
    """
    Give up on the courier holding this order, without replacing them.

    The way out of the one refusal `fulfilment_reassignment.refuse` makes on
    purpose: a driver has been matched and has stopped moving. Its own action
    rather than folded into the move, because calling off a booking with a
    driver on it can cost a fee, and a press whose headline is "change courier"
    should not spend money as a side effect.

    Leaves the order on the same provider with no booking — a state every
    dispatch path already understands. `needs_attention` picks it up, re-dispatch
    works, and a move to a different courier is now allowed.
    """
    order = await _load_order(db, order_number)
    delivery = await _load_delivery(db, order_number)

    exposure = await fulfilment_reassignment.exposure_of(db, delivery)
    if exposure.will_be_charged and not data.acknowledged_charge:
        raise ConflictError(
            exposure.reason,
            payload={
                "message": exposure.reason,
                "requires_acknowledgement": True,
            },
        )

    booking = delivery.courier_order_id
    provider = delivery.provider
    delivery = await fulfilment_reassignment.abandon_booking(db, order)

    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="order_delivery",
        entity_id=order_number,
        entity_label=order_number,
        admin=admin,
        changes={
            "abandoned_booking": {"provider": provider, "courier_order_id": booking},
            "may_be_charged": exposure.will_be_charged,
        },
        request=request,
    )
    return await _delivery_payload(db, order, delivery)


class LalamoveAssignRequest(BaseModel):
    #: The id from the quote the admin just agreed to. Required, and checked,
    #: so a client cannot book at a price nobody was shown.
    quotation_id: str


@router.post(
    "/{order_number}/delivery/lalamove/quote", response_model=LalamoveQuoteResponse
)
async def quote_lalamove_for_order(
    order_number: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("orders.manage")),
):
    """
    What Lalamove would charge to carry this third-party order.

    **Superseded by `/delivery/fulfilment-quote`,** which asks the same question
    of any courier the order's zone allows. Kept, and kept behaving exactly as it
    did, because an admin tab opened before the new dialog shipped is still a
    perfectly ordinary thing to press — and because a narrowing of scope is not
    something a stale client should discover as a 404.
    """
    order = await _load_order(db, order_number)
    delivery = await _load_delivery(db, order_number)
    _assert_assignable(order, delivery)

    quote, error = await lalamove_service.quote_for_order(db, order)
    if quote is None:
        raise BadGatewayError(error or "No price available")
    return _quote_response(quote, delivery)


@router.post(
    "/{order_number}/delivery/lalamove/assign", response_model=OrderDeliveryResponse
)
async def assign_order_to_lalamove(
    order_number: str,
    data: LalamoveAssignRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("orders.manage")),
):
    """
    Hand this packed third-party order to Lalamove, at the price just quoted.

    **Superseded by `/delivery/reassign`** with `provider="lalamove"`, which is
    what this now calls. The gates are the general ones from that path rather
    than this endpoint's old `packed`-and-third-party-only pair — deliberately:
    two sets of rules about when an order may change hands is one set too many,
    and the newer set is the argued one.
    """
    return await reassign_order_fulfilment(
        order_number,
        FulfilmentReassignRequest(
            provider=FulfilmentProviderEnum.LALAMOVE.value,
            quotation_id=data.quotation_id,
        ),
        request,
        db,
        admin,
    )


class OrderStatusEventResponse(BaseModel):
    """One transition, with who caused it. **Admin only.**

    Separate from the `status_history` on `OrderResponse` for the same reason
    `OrderDeliveryResponse` is separate from it: that model goes to the
    customer, and who moved their order and from which register is not theirs.
    """

    status: str
    previous_status: str | None
    at: datetime
    source: str
    actor_label: str | None
    note: str | None


@router.get(
    "/{order_number}/status-events", response_model=list[OrderStatusEventResponse]
)
async def list_order_status_events(
    order_number: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("orders.read")),
):
    """Every status this order has been through, oldest first, and who moved it."""
    order = await _load_order(db, order_number)
    rows = (
        (
            await db.execute(
                select(OrderStatusEvent)
                .where(OrderStatusEvent.order_id == order.id)
                .order_by(OrderStatusEvent.at)
            )
        )
        .scalars()
        .all()
    )
    return [
        OrderStatusEventResponse(
            status=row.status,
            previous_status=row.previous_status,
            at=row.at,
            source=row.source,
            actor_label=row.actor_label,
            note=row.note,
        )
        for row in rows
    ]


#: A far-future sentinel so a marketplace step with no timestamp sorts to the end
#: (ordered among its peers by `sequence`) rather than jumping to the front.
_FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)


@router.get("/{order_number}/details", response_model=OrderAdminDetails)
async def order_admin_details(
    order_number: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("orders.read")),
):
    """The admin order-details enrichment: the fulfilling branch, the marketplace
    payment type, and one unified status timeline.

    The timeline merges MM's own lifecycle (`order_status_events` — who moved it,
    from where, with what note) and, for an aggregator order, the marketplace's
    verbatim trace (`aggregator_order_status_event` — "finding courier", "rider
    near pickup", …), linked reliably through the promoted order's
    `aggregator_order.mm_order_id`, not a display-name guess. Everything is
    admin-only and lives here rather than on the customer-facing `OrderResponse`.
    A section the order has no data for simply comes back empty/null, so the same
    page renders every order type and shows only what exists.
    """
    order = await _load_order(db, order_number)

    branch = await db.get(Branch, order.branch_id) if order.branch_id else None

    entries: list[OrderTimelineEntry] = [
        OrderTimelineEntry(
            origin="mm",
            status=row.status,
            at=row.at,
            source=row.source,
            actor_label=row.actor_label,
            note=row.note,
        )
        for row in (
            await db.execute(
                select(OrderStatusEvent)
                .where(OrderStatusEvent.order_id == order.id)
                .order_by(OrderStatusEvent.at)
            )
        )
        .scalars()
        .all()
    ]

    # The marketplace trace, reached through the promoted aggregator order (the
    # only reliable link — order.aggregator_channel is a display name, the event's
    # channel is the code).
    agg = await db.scalar(
        select(AggregatorOrder).where(AggregatorOrder.mm_order_id == order.id)
    )
    if agg is not None:
        trace = (
            (
                await db.execute(
                    select(AggregatorOrderStatusEvent)
                    .where(
                        AggregatorOrderStatusEvent.channel == agg.channel,
                        AggregatorOrderStatusEvent.external_order_id
                        == agg.external_order_id,
                    )
                    .order_by(
                        AggregatorOrderStatusEvent.at,
                        AggregatorOrderStatusEvent.sequence,
                    )
                )
            )
            .scalars()
            .all()
        )
        entries.extend(
            OrderTimelineEntry(
                origin="marketplace",
                status=ev.status,
                at=ev.at,
                source=agg.channel,
                sequence=ev.sequence,
            )
            for ev in trace
        )

    entries.sort(key=lambda e: (e.at or _FAR_FUTURE, e.sequence or 0))

    return OrderAdminDetails(
        branch=OrderBranchSummary.model_validate(branch) if branch else None,
        aggregator_payment_type=order.aggregator_payment_type,
        timeline=entries,
    )
