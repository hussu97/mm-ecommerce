"""
Custom orders: taking, making and finishing a bespoke cake.

A custom order is an ordinary order (`orders.source = 'custom'`) at the one
branch configured to make them. Everything a channel needs that already exists
is reused rather than copied:

* money — lines and the card-fee charge are priced by the register's one money
  writer, `pos_order_service.recalculate` (VAT, legal entity, order taxes); the
  card processor's fee by `order_fees`;
* status — every move goes through `order_lifecycle.transition()`, whose
  consequences for this channel (`orders.channels`) are: consume the order's own
  recipe when it is packed, stamp the trading day when it is handed over, call
  off any courier we booked when it is cancelled;
* couriers — a Slider or Lalamove booking is made by
  `delivery.fulfilment_reassignment.book_first`, after which the storefront's
  courier machinery (webhooks, tracking, customer emails) carries it.

What is only here: the order's own fields (`custom_orders`), its recipe, the
kitchen-docket claim that puts it "at the POS", and the three ways it finishes
by hand — a third-party courier, a collection, a cancellation.

Services flush; the request commits.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, cast, func, select
from sqlalchemy import update as sql_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import search as search_text
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.money import money, to_decimal
from app.core.phone import describe_phone
from app.core.trading_hours import DELIVERY_TIMEZONE
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.business_settings import BusinessSettings
from app.models.charge import Charge
from app.models.custom_order import (
    CustomOrder,
    CustomOrderCardFeeModeEnum,
    CustomOrderPaymentTypeEnum,
    CustomOrderRecipeLine,
)
from app.models.custom_order_enquiry import CustomOrderEnquiry
from app.models.inventory import InventoryItem, InventoryLevel, Warehouse
from app.models.order import DeliveryMethodEnum, Order, OrderItem, OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.models.order_status_event import StatusSourceEnum, acting_as
from app.models.pos_order import OrderCharge, OrderSourceEnum, OrderTypeEnum
from app.models.product import Product
from app.models.user import User
from app.schemas.custom_order import (
    CustomCakeItem,
    CustomOrderActions,
    CustomOrderAddressIn,
    CustomOrderAddressOut,
    CustomOrderChargeOut,
    CustomOrderCreate,
    CustomOrderCustomerIn,
    CustomOrderDeliveryOut,
    CustomOrderLineIn,
    CustomOrderLineOut,
    CustomOrderListItem,
    CustomOrderRecipeLineIn,
    CustomOrderRecipeLineOut,
    CustomOrderResponse,
    CustomOrderUpdate,
)
from app.services.orders import order_fees, order_lifecycle

#: The inactive charge (migration 295) a card fee is billed as, so it is taxed
#: like the cake and no till ever offers it.
CARD_FEE_CHARGE_REFERENCE = "custom-order-card-fee"
CARD_FEE_LINE_NAME = "Card payment fee"

#: The card processor a custom order's card fee is priced at.
CARD_PROVIDER = "stripe"

#: A delivery date with no time is a promise for the day; noon keeps it on that
#: day in every report whatever the time zone arithmetic.
_DAY_PROMISE_TIME = time(12, 0)

_TZ = ZoneInfo(DELIVERY_TIMEZONE)

#: Status groups the lists are filtered by.
STATUS_GROUPS: dict[str, tuple[OrderStatusEnum, ...]] = {
    "pending": (
        OrderStatusEnum.CREATED,
        OrderStatusEnum.CONFIRMED,
        OrderStatusEnum.ARRIVED_AT_POS,
    ),
    "packed": (OrderStatusEnum.PACKED, OrderStatusEnum.UNDELIVERED),
    "on_the_way": (OrderStatusEnum.OUT_FOR_DELIVERY,),
    "delivered": (OrderStatusEnum.DELIVERED,),
    "cancelled": (OrderStatusEnum.CANCELLED, OrderStatusEnum.REFUNDED),
}

_BEFORE_PACKING = frozenset(STATUS_GROUPS["pending"])
_READY_TO_FINISH = frozenset({OrderStatusEnum.PACKED, OrderStatusEnum.UNDELIVERED})
_FINISHED = frozenset(
    {
        OrderStatusEnum.DELIVERED,
        OrderStatusEnum.CANCELLED,
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.DISPUTED,
    }
)

#: Couriers we book over an API. A row for one with a live booking means a
#: rider has been engaged, so the order's contact and address are fixed.
_BOOKED_PROVIDERS = frozenset({"lalamove", "slider_car", "slider_bike", "noon_send"})


class CustomOrdersDisabled(ConflictError):
    """The channel has no branch, product or inventory category configured."""


# ─── Configuration ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChannelConfig:
    branch: Branch
    product: Product
    category_id: uuid.UUID


async def config(db: AsyncSession) -> ChannelConfig:
    """The branch, product and inventory category the channel runs on."""
    settings = (
        await db.execute(select(BusinessSettings).limit(1))
    ).scalar_one_or_none()
    branch = (
        await db.get(Branch, settings.custom_orders_branch_id)
        if settings and settings.custom_orders_branch_id
        else None
    )
    product = (
        await db.get(Product, settings.custom_orders_product_id)
        if settings and settings.custom_orders_product_id
        else None
    )
    category_id = settings.custom_orders_inventory_category_id if settings else None
    missing = [
        label
        for label, value in (
            ("branch", branch),
            ("product", product),
            ("inventory category", category_id),
        )
        if value is None
    ]
    if missing:
        raise CustomOrdersDisabled(
            "Custom orders are not set up: choose their "
            + ", ".join(missing)
            + " in Settings"
        )
    return ChannelConfig(branch=branch, product=product, category_id=category_id)


async def branch_id(db: AsyncSession) -> uuid.UUID | None:
    """The custom-orders branch, or None when the channel is off. Never raises."""
    settings = (
        await db.execute(select(BusinessSettings).limit(1))
    ).scalar_one_or_none()
    return settings.custom_orders_branch_id if settings else None


async def assert_register_branch(db: AsyncSession, device_branch_id: uuid.UUID) -> None:
    """A register may act on custom orders only at the custom-orders branch."""
    if await branch_id(db) != device_branch_id:
        raise ConflictError("Custom orders are taken at the custom-orders branch only")


# ─── Reading ───────────────────────────────────────────────────────────────────


async def get(db: AsyncSession, order_number: str) -> tuple[Order, CustomOrder]:
    """The order and its custom half, fully loaded, or NotFound."""
    from app.services.pos import pos_order_service

    order_id = (
        await db.execute(
            select(Order.id).where(
                Order.order_number == order_number,
                Order.source == OrderSourceEnum.CUSTOM.value,
            )
        )
    ).scalar_one_or_none()
    if order_id is None:
        raise NotFoundError("Custom order not found")
    return await _load(db, order_id, pos_order_service)


async def get_by_id(db: AsyncSession, order_id: uuid.UUID) -> tuple[Order, CustomOrder]:
    from app.services.pos import pos_order_service

    return await _load(db, order_id, pos_order_service)


async def _load(db, order_id, pos_order_service) -> tuple[Order, CustomOrder]:
    order = await pos_order_service.get_order(db, order_id)
    custom = await db.get(CustomOrder, order.id, populate_existing=True)
    if custom is None or order.source != OrderSourceEnum.CUSTOM.value:
        raise NotFoundError("Custom order not found")
    return order, custom


async def list_orders(
    db: AsyncSession,
    *,
    status_group: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[tuple[Order, CustomOrder]], int]:
    """Custom orders, soonest delivery first; filters by status group, the
    delivery-date window (inclusive, shop-local) and a free-text search."""
    stmt = (
        select(Order, CustomOrder)
        .join(CustomOrder, CustomOrder.order_id == Order.id)
        .where(Order.source == OrderSourceEnum.CUSTOM.value)
    )
    if status_group:
        statuses = STATUS_GROUPS.get(status_group)
        if statuses is None:
            raise BadRequestError(f"Unknown status group '{status_group}'")
        stmt = stmt.where(Order.status.in_(statuses))
    if date_from is not None:
        stmt = stmt.where(Order.promised_at >= _start_of(date_from))
    if date_to is not None:
        stmt = stmt.where(Order.promised_at < _start_of(date_to, days=1))
    if q and q.strip():
        stmt = stmt.where(
            search_text.contains(Order.order_number, q)
            | search_text.contains(Order.customer_name, q)
            | search_text.contains(Order.customer_phone, q)
            | search_text.contains(Order.email, q)
            | Order.id.in_(
                select(OrderItem.order_id).where(
                    search_text.contains(OrderItem.product_name, q)
                )
            )
        )
    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(Order.promised_at.asc().nulls_last(), Order.created_at.asc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).all()
    return [(row[0], row[1]) for row in rows], int(total)


async def first_line_titles(
    db: AsyncSession, order_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Each order's first line title, for list summaries."""
    if not order_ids:
        return {}
    rows = (
        await db.execute(
            select(OrderItem.order_id, OrderItem.product_name, OrderItem.created_at)
            .where(OrderItem.order_id.in_(order_ids))
            .order_by(OrderItem.order_id, OrderItem.created_at, OrderItem.id)
        )
    ).all()
    titles: dict[uuid.UUID, str] = {}
    counts: dict[uuid.UUID, int] = {}
    for order_id, title, _ in rows:
        counts[order_id] = counts.get(order_id, 0) + 1
        titles.setdefault(order_id, title)
    return {
        order_id: title + (f" +{counts[order_id] - 1}" if counts[order_id] > 1 else "")
        for order_id, title in titles.items()
    }


async def custom_cake_items(
    db: AsyncSession, *, only: set[uuid.UUID] | None = None
) -> list[tuple[InventoryItem, Decimal]]:
    """The items a recipe may use and the branch may produce for custom orders,
    with each one's on-hand at the branch's default warehouse (storage units)."""
    cfg = await config(db)
    stmt = select(InventoryItem).where(
        InventoryItem.category_id == cfg.category_id,
        InventoryItem.is_active.is_(True),
    )
    if only is not None:
        stmt = stmt.where(InventoryItem.id.in_(only))
    items = list((await db.execute(stmt.order_by(InventoryItem.name))).scalars().all())
    warehouse_id = (
        await db.execute(
            select(Warehouse.id).where(
                Warehouse.branch_id == cfg.branch.id, Warehouse.is_default.is_(True)
            )
        )
    ).scalar_one_or_none()
    levels: dict[uuid.UUID, Decimal] = {}
    if warehouse_id is not None and items:
        levels = {
            item_id: to_decimal(quantity)
            for item_id, quantity in (
                await db.execute(
                    select(InventoryLevel.item_id, InventoryLevel.quantity).where(
                        InventoryLevel.warehouse_id == warehouse_id,
                        InventoryLevel.item_id.in_([i.id for i in items]),
                    )
                )
            ).all()
        }
    return [(item, levels.get(item.id, Decimal("0"))) for item in items]


# ─── Taking an order ───────────────────────────────────────────────────────────


async def create(
    db: AsyncSession, data: CustomOrderCreate, *, user: User, via: str
) -> Order:
    """Take a custom order: price it, and confirm it in the same request.

    `via` is `admin` or `pos`. A replay of the same `client_request_id` returns
    the order the first attempt made.
    """
    cfg = await config(db)

    if data.client_request_id is not None:
        existing = (
            await db.execute(
                select(Order).where(Order.client_request_id == data.client_request_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    if data.enquiry_id is not None:
        if await db.get(CustomOrderEnquiry, data.enquiry_id) is None:
            raise NotFoundError("Enquiry not found")
        converted = (
            await db.execute(
                select(CustomOrder.order_id).where(
                    CustomOrder.enquiry_id == data.enquiry_id
                )
            )
        ).scalar_one_or_none()
        if converted is not None:
            raise ConflictError("That enquiry has already been converted to an order")

    await _check_recipe_items(db, cfg, data.recipe)

    order = Order(
        order_number="",
        client_request_id=data.client_request_id,
        email="",
        delivery_method=DeliveryMethodEnum.PICKUP,
        order_type=OrderTypeEnum.PICKUP.value,
        status=OrderStatusEnum.CREATED,
        source=OrderSourceEnum.CUSTOM.value,
        branch_id=cfg.branch.id,
        locale="en",
        is_pos=False,
        creator_id=user.id,
        subtotal=Decimal("0"),
        discount_amount=Decimal("0"),
        delivery_fee=Decimal("0"),
        total=Decimal("0"),
        vat_amount=Decimal("0"),
        total_excl_vat=Decimal("0"),
        notes=data.notes,
    )
    _apply_promise(order, data.delivery_date, data.delivery_time)
    _apply_contact(order, data.customer, data.address)
    _apply_payment(order, data.payment_type)
    await _insert_with_number(db, order)

    custom = CustomOrder(
        order_id=order.id,
        payment_type=data.payment_type,
        card_fee_mode=data.card_fee_mode,
        enquiry_id=data.enquiry_id,
        created_via=via,
        created_by_id=user.id,
    )
    db.add(custom)
    _replace_recipe(db, custom, data.recipe)
    _add_lines(db, order, cfg.product, data.lines, user)
    await db.flush()
    order, custom = await get_by_id(db, order.id)
    await _reprice(db, order, custom)

    with acting_as(_status_source(via), actor_id=user.id, actor_label=user.email):
        order, _ = await get_by_id(db, order.id)
        await order_lifecycle.transition(db, order, OrderStatusEnum.CONFIRMED)
    await db.flush()
    return order


async def _insert_with_number(db: AsyncSession, order: Order) -> None:
    """Insert under the next `CO-YYYYMMDD-NNN`, retrying a lost race."""
    for attempt in range(3):
        order.order_number = await _next_number(db)
        try:
            async with db.begin_nested():
                db.add(order)
                await db.flush()
            return
        except IntegrityError:
            if attempt == 2:
                raise


async def _next_number(db: AsyncSession) -> str:
    """`CO-` and the shop's date, numbered from 001 each day. Also the
    invoice number, so it is a series of its own (not the storefront's MM-)."""
    prefix = f"CO-{datetime.now(_TZ).strftime('%Y%m%d')}-"
    last = (
        await db.execute(
            select(
                func.max(cast(func.split_part(Order.order_number, "-", 3), Integer))
            ).where(Order.order_number.like(f"{prefix}%"))
        )
    ).scalar_one_or_none()
    return f"{prefix}{int(last or 0) + 1:03d}"


def _status_source(via: str) -> str:
    return StatusSourceEnum.POS.value if via == "pos" else StatusSourceEnum.ADMIN.value


def _start_of(day: date, *, days: int = 0) -> datetime:
    from datetime import timedelta

    return datetime.combine(day + timedelta(days=days), time(0, 0), tzinfo=_TZ)


def _apply_promise(order: Order, day: date, at: time | None) -> None:
    order.promised_at = datetime.combine(day, at or _DAY_PROMISE_TIME, tzinfo=_TZ)
    order.promised_precision = "time" if at is not None else "day"


def _apply_contact(
    order: Order,
    customer: CustomOrderCustomerIn,
    address: CustomOrderAddressIn | None,
) -> None:
    """Customer on the order's own columns, address as the snapshot every
    courier and screen already reads. Any combination may be empty."""
    name = (customer.name or "").strip() or None
    phone = describe_phone(customer.phone) if customer.phone else None
    if customer.phone and not (phone and phone.e164):
        raise BadRequestError("That phone number is not valid")
    order.customer_name = name
    order.customer_phone = phone.e164 if phone else None
    order.customer_phone_country = phone.country if phone else None
    order.customer_phone_type = phone.type if phone else None
    # `orders.email` is NOT NULL; an order without one stores "" (as the till
    # does), which the mailer treats as nobody to write to.
    order.email = str(customer.email).strip().lower() if customer.email else ""

    has_address = address is not None and any(
        value not in (None, "")
        for value in (
            address.latitude,
            address.address_line_1,
            address.unit_number,
        )
    )
    if not has_address:
        order.shipping_address_snapshot = None
    else:
        first, _, last = (name or "").partition(" ")
        order.shipping_address_snapshot = {
            "first_name": first,
            "last_name": last,
            "phone": order.customer_phone or "",
            "address_line_1": (address.address_line_1 or "").strip(),
            "unit_number": (address.unit_number or "").strip() or None,
            "latitude": address.latitude,
            "longitude": address.longitude,
            "country": "AE",
        }
    pinned = has_address and address.latitude is not None
    order.delivery_method = (
        DeliveryMethodEnum.DELIVERY if pinned else DeliveryMethodEnum.PICKUP
    )
    order.order_type = (
        OrderTypeEnum.DELIVERY.value if pinned else OrderTypeEnum.PICKUP.value
    )


def _apply_payment(order: Order, payment_type: str | None) -> None:
    """Never `cod`: a custom order is paid to the shop, never to a rider, so no
    courier is ever asked to collect (Slider sends `cod_amount` only for cod)."""
    if payment_type == CustomOrderPaymentTypeEnum.CARD.value:
        order.payment_method = "card"
        order.payment_provider = CARD_PROVIDER
    else:
        order.payment_method = payment_type
        order.payment_provider = None


def _add_lines(
    db: AsyncSession,
    order: Order,
    product: Product,
    lines: list[CustomOrderLineIn],
    user: User,
) -> None:
    """Each line is the channel's open-price product under its own title.

    `status` stays NULL, as on every line not rung up at a till."""
    now = utcnow()
    for line in lines:
        price = money(line.unit_price)
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                product_name=line.title.strip(),
                product_sku=product.sku or "",
                product_translations={},
                quantity=line.quantity,
                base_price=price,
                options_price=Decimal("0"),
                unit_price=price,
                total_price=money(price * line.quantity),
                selected_options_snapshot=[],
                is_open_price=True,
                kitchen_notes=(line.notes or "").strip() or None,
                creator_id=user.id,
                added_at=now,
            )
        )


def _replace_recipe(
    db: AsyncSession, custom: CustomOrder, recipe: list[CustomOrderRecipeLineIn]
) -> None:
    custom.recipe_lines.clear()
    seen: set[uuid.UUID] = set()
    for position, line in enumerate(recipe):
        if line.item_id in seen:
            raise BadRequestError("Each item may appear in the recipe once")
        seen.add(line.item_id)
        custom.recipe_lines.append(
            CustomOrderRecipeLine(
                item_id=line.item_id, quantity=line.quantity, position=position
            )
        )


async def _check_recipe_items(
    db: AsyncSession, cfg: ChannelConfig, recipe: list[CustomOrderRecipeLineIn]
) -> None:
    ids = {line.item_id for line in recipe}
    if not ids:
        return
    allowed = {
        item_id
        for item_id in (
            await db.execute(
                select(InventoryItem.id).where(
                    InventoryItem.id.in_(ids),
                    InventoryItem.category_id == cfg.category_id,
                )
            )
        )
        .scalars()
        .all()
    }
    if ids - allowed:
        raise BadRequestError(
            "A custom order's recipe may only use Customized Cake Raw Materials"
        )


async def _reprice(db: AsyncSession, order: Order, custom: CustomOrder) -> None:
    """Price the lines, add the card-fee line if one is billed, and record what
    taking the card costs.

    Two passes when a fee line is billed: the goods first, so the fee can be
    sized to cover the processor's fee on the goods and on itself
    (`order_fees.card_fee_covering`), then the whole order."""
    from app.services.pos import pos_order_service

    for charge in list(order.order_charges):
        await db.delete(charge)
    await db.flush()
    order = await pos_order_service.recalculate(db, order)

    if custom.card_fee_mode == CustomOrderCardFeeModeEnum.SEPARATE_LINE.value:
        fraction, fixed = await order_fees.card_fee_terms(db, order.payment_provider)
        fee_line = order_fees.card_fee_covering(
            to_decimal(order.total), fraction, fixed
        )
        charge_id = (
            await db.execute(
                select(Charge.id).where(Charge.reference == CARD_FEE_CHARGE_REFERENCE)
            )
        ).scalar_one_or_none()
        db.add(
            OrderCharge(
                order_id=order.id,
                charge_id=charge_id,
                name=CARD_FEE_LINE_NAME,
                type="fixed",
                value=fee_line,
                amount=Decimal("0"),  # filled by recalculate
            )
        )
        await db.flush()
        order = await pos_order_service.recalculate(db, order)

    await order_fees.stamp(db, order)
    await db.flush()


# ─── Changing an order ─────────────────────────────────────────────────────────


async def update(
    db: AsyncSession,
    order: Order,
    custom: CustomOrder,
    data: CustomOrderUpdate,
    *,
    user: User,
) -> Order:
    """Replace the lines, delivery date, payment and notes, and re-price."""
    if order.status in _FINISHED:
        raise ConflictError("A delivered or cancelled custom order can't be changed")
    cfg = await config(db)
    for item in list(order.items):
        await db.delete(item)
    await db.flush()
    _apply_promise(order, data.delivery_date, data.delivery_time)
    _apply_payment(order, data.payment_type)
    order.notes = data.notes
    custom.payment_type = data.payment_type
    custom.card_fee_mode = data.card_fee_mode
    _add_lines(db, order, cfg.product, data.lines, user)
    await db.flush()
    order, custom = await get_by_id(db, order.id)
    await _reprice(db, order, custom)
    return order


async def update_contact(
    db: AsyncSession,
    order: Order,
    *,
    customer: CustomOrderCustomerIn,
    address: CustomOrderAddressIn | None,
) -> Order:
    """The customer and address may be filled in until a courier is booked."""
    reason = await contact_locked_reason(db, order)
    if reason:
        raise ConflictError(reason)
    _apply_contact(order, customer, address)
    await db.flush()
    return order


async def update_recipe(
    db: AsyncSession,
    order: Order,
    custom: CustomOrder,
    recipe: list[CustomOrderRecipeLineIn],
) -> None:
    if order.status not in _BEFORE_PACKING:
        raise ConflictError("The recipe was consumed when the order was packed")
    await _check_recipe_items(db, await config(db), recipe)
    _replace_recipe(db, custom, recipe)
    await db.flush()


# ─── Moving an order ───────────────────────────────────────────────────────────


async def claim_print(db: AsyncSession, order: Order, *, user: User) -> bool:
    """First register to claim the kitchen docket prints it; the order is then
    "at the POS". A conditional UPDATE, so two registers woken by the same push
    cannot both print. Returns whether this caller won."""
    won = (
        await db.execute(
            sql_update(CustomOrder)
            .where(
                CustomOrder.order_id == order.id,
                CustomOrder.kitchen_printed_at.is_(None),
            )
            .values(kitchen_printed_at=utcnow())
            .returning(CustomOrder.order_id)
        )
    ).scalar_one_or_none() is not None
    if won and order.status == OrderStatusEnum.CONFIRMED:
        with acting_as(
            StatusSourceEnum.POS.value, actor_id=user.id, actor_label=user.email
        ):
            await order_lifecycle.transition(db, order, OrderStatusEnum.ARRIVED_AT_POS)
    await db.flush()
    return won


async def unprinted(db: AsyncSession) -> list[Order]:
    """Orders whose docket no register has claimed yet (a push missed while the
    iPad slept, a till that was closed)."""
    rows = (
        await db.execute(
            select(Order)
            .join(CustomOrder, CustomOrder.order_id == Order.id)
            .where(
                CustomOrder.kitchen_printed_at.is_(None),
                Order.status.in_(tuple(_BEFORE_PACKING)),
            )
            .order_by(Order.created_at)
        )
    ).scalars()
    return list(rows.all())


async def pack(db: AsyncSession, order: Order, *, user: User, via: str) -> Order:
    """Boxed and ready. Consumes the recipe (the lifecycle's packing consequence)."""
    if order.status not in _BEFORE_PACKING:
        raise ConflictError(f"A {order.status.value} order can't be packed")
    with acting_as(_status_source(via), actor_id=user.id, actor_label=user.email):
        if order.status == OrderStatusEnum.CREATED:
            await order_lifecycle.transition(db, order, OrderStatusEnum.CONFIRMED)
        await order_lifecycle.transition(db, order, OrderStatusEnum.PACKED)
    await db.flush()
    return order


async def mark_collected(
    db: AsyncSession, order: Order, *, user: User, via: str
) -> Order:
    """The customer took it from the shop. No courier, no cost, no email."""
    await _finish_by_hand(db, order, user=user, via=via)
    return order


async def finish_third_party(
    db: AsyncSession, order: Order, *, courier_fee: Decimal, user: User
) -> Order:
    """A courier we did not book carried it. Its fee is the order's delivery
    cost (VAT inclusive, like every courier's); the order is delivered."""
    _assert_ready_to_finish(order)
    await _call_off_live_booking(db, order)
    delivery = (
        await db.execute(
            select(OrderDelivery).where(OrderDelivery.order_id == order.id)
        )
    ).scalar_one_or_none()
    fee = money(courier_fee)
    if delivery is None:
        delivery = OrderDelivery(order_id=order.id, provider="third_party")
        db.add(delivery)
    else:
        if delivery.provider != "third_party":
            delivery.original_provider = delivery.original_provider or delivery.provider
        delivery.provider = "third_party"
    delivery.quoted_cost = fee
    delivery.cost_total = fee
    await _finish_by_hand(db, order, user=user, via="admin")
    return order


def _assert_ready_to_finish(order: Order) -> None:
    if order.status not in _READY_TO_FINISH:
        raise ConflictError("Pack the order before it is delivered or collected")


async def _finish_by_hand(
    db: AsyncSession, order: Order, *, user: User, via: str
) -> None:
    _assert_ready_to_finish(order)
    await _call_off_live_booking(db, order)
    with acting_as(_status_source(via), actor_id=user.id, actor_label=user.email):
        if order.status == OrderStatusEnum.UNDELIVERED:
            # The map sends a failed hand-over back through `packed`.
            await order_lifecycle.transition(db, order, OrderStatusEnum.PACKED)
        await order_lifecycle.transition(db, order, OrderStatusEnum.DELIVERED)
    await db.flush()


async def _call_off_live_booking(db: AsyncSession, order: Order) -> None:
    """A change of mind after booking: the courier we engaged is cancelled
    before the order is finished another way."""
    from app.services.couriers import courier_service

    if await _live_booking(db, order) is not None:
        await courier_service.cancel(db, order)


async def cancel(db: AsyncSession, order: Order, *, user: User, via: str) -> Order:
    """Cancel before hand-over. What packing consumed stays consumed."""
    with acting_as(_status_source(via), actor_id=user.id, actor_label=user.email):
        await order_lifecycle.transition(
            db,
            order,
            OrderStatusEnum.CANCELLED,
            extra_from=order_lifecycle.CUSTOM_CANCELLABLE_FROM,
        )
    await db.flush()
    return order


# ─── What may be done now ──────────────────────────────────────────────────────


async def _live_booking(db: AsyncSession, order: Order) -> OrderDelivery | None:
    delivery = (
        await db.execute(
            select(OrderDelivery).where(OrderDelivery.order_id == order.id)
        )
    ).scalar_one_or_none()
    if (
        delivery is not None
        and delivery.provider in _BOOKED_PROVIDERS
        and delivery.courier_order_id
        and delivery.cancelled_at is None
    ):
        return delivery
    return None


async def contact_locked_reason(db: AsyncSession, order: Order) -> str | None:
    if order.status in _FINISHED or order.status == OrderStatusEnum.OUT_FOR_DELIVERY:
        return "The order has left the shop"
    if await _live_booking(db, order) is not None:
        return "A courier has been booked with these details"
    return None


def delivery_unavailable_reason(order: Order) -> str | None:
    """Why a courier cannot be booked for this order yet, if it cannot."""
    missing = []
    address = order.shipping_address_snapshot or {}
    if address.get("latitude") is None or address.get("longitude") is None:
        missing.append("a location pin")
    if not order.customer_name:
        missing.append("the customer's name")
    if not order.customer_phone:
        missing.append("the customer's phone")
    if missing:
        return "A courier needs " + ", ".join(missing)
    return None


def delivery_date_parts(order: Order) -> tuple[date | None, time | None]:
    if order.promised_at is None:
        return None, None
    local = order.promised_at.astimezone(_TZ)
    return local.date(), (local.time() if order.promised_precision == "time" else None)


# ─── Responses ─────────────────────────────────────────────────────────────────


async def to_response(
    db: AsyncSession, order: Order, custom: CustomOrder
) -> CustomOrderResponse:
    """The order as every custom-order screen reads it, with what may be done to
    it now decided here so the console and both registers agree."""
    from app.services.orders import custom_order_invoice

    delivery_date, delivery_time = delivery_date_parts(order)
    address = order.shipping_address_snapshot or None
    delivery = (
        await db.execute(
            select(OrderDelivery).where(OrderDelivery.order_id == order.id)
        )
    ).scalar_one_or_none()

    recipe_ids = {line.item_id for line in custom.recipe_lines}
    stock = {
        item.id: (item, on_hand)
        for item, on_hand in (
            await custom_cake_items(db, only=recipe_ids) if recipe_ids else []
        )
    }
    recipe_out = []
    for line in custom.recipe_lines:
        item, on_hand = stock.get(line.item_id, (None, Decimal("0")))
        if item is None:
            item = await db.get(InventoryItem, line.item_id)
        recipe_out.append(
            CustomOrderRecipeLineOut(
                item_id=line.item_id,
                sku=getattr(item, "sku", None),
                name=getattr(item, "name", "Unknown item"),
                unit=getattr(item, "ingredient_unit", None),
                quantity=to_decimal(line.quantity),
                on_hand=item.to_ingredient_units(on_hand) if item else Decimal("0"),
            )
        )

    contact_locked = await contact_locked_reason(db, order)
    no_courier = delivery_unavailable_reason(order)
    status = order.status
    actions = CustomOrderActions(
        can_edit_lines=status not in _FINISHED,
        can_edit_recipe=status in _BEFORE_PACKING,
        can_edit_contact=contact_locked is None,
        can_pack=status in _BEFORE_PACKING,
        can_collect=status in _READY_TO_FINISH,
        can_choose_delivery=status in _READY_TO_FINISH and no_courier is None,
        can_cancel=status
        in (
            _BEFORE_PACKING
            | order_lifecycle.CUSTOM_CANCELLABLE_FROM
            | {OrderStatusEnum.UNDELIVERED}
        ),
        delivery_unavailable_reason=no_courier,
        invoice_unavailable_reason=custom_order_invoice.invoice_readiness(order),
    )

    return CustomOrderResponse(
        id=order.id,
        order_number=order.order_number,
        status=status.value,
        created_via=custom.created_via,
        created_at=order.created_at,
        delivery_date=delivery_date,
        delivery_time=delivery_time,
        delivered_at=order.delivered_at,
        kitchen_printed_at=custom.kitchen_printed_at,
        customer_name=order.customer_name,
        customer_email=order.email or None,
        customer_phone=order.customer_phone,
        address=(
            CustomOrderAddressOut(
                latitude=address.get("latitude"),
                longitude=address.get("longitude"),
                address_line_1=address.get("address_line_1") or None,
                unit_number=address.get("unit_number") or None,
            )
            if address
            else None
        ),
        payment_type=custom.payment_type,
        card_fee_mode=custom.card_fee_mode,
        notes=order.notes,
        enquiry_id=custom.enquiry_id,
        lines=[
            CustomOrderLineOut(
                id=item.id,
                title=item.product_name,
                quantity=item.quantity,
                unit_price=to_decimal(item.unit_price),
                total=to_decimal(item.total_price),
                notes=item.kitchen_notes,
            )
            for item in sorted(order.items, key=lambda i: (i.created_at, str(i.id)))
        ],
        charges=[
            CustomOrderChargeOut(name=charge.name, amount=to_decimal(charge.amount))
            for charge in order.order_charges
        ],
        recipe=recipe_out,
        subtotal=to_decimal(order.subtotal),
        charges_amount=to_decimal(order.charges_amount),
        vat_amount=to_decimal(order.vat_amount),
        total_excl_vat=to_decimal(order.total_excl_vat),
        total=to_decimal(order.total),
        payment_fee=(
            to_decimal(order.payment_fee) if order.payment_fee is not None else None
        ),
        delivery=(
            CustomOrderDeliveryOut(
                provider=delivery.provider,
                courier_status=delivery.courier_status,
                cost=(
                    to_decimal(delivery.cost_total)
                    if delivery.cost_total is not None
                    else (
                        to_decimal(delivery.quoted_cost)
                        if delivery.quoted_cost is not None
                        else None
                    )
                ),
                share_link=delivery.share_link,
                driver_name=delivery.driver_name,
                driver_phone=delivery.driver_phone,
                last_error=delivery.last_error,
            )
            if delivery is not None
            else None
        ),
        actions=actions,
    )


async def to_list_items(
    db: AsyncSession, rows: list[tuple[Order, CustomOrder]]
) -> list[CustomOrderListItem]:
    titles = await first_line_titles(db, [order.id for order, _ in rows])
    providers = (
        dict(
            (
                await db.execute(
                    select(OrderDelivery.order_id, OrderDelivery.provider).where(
                        OrderDelivery.order_id.in_([order.id for order, _ in rows])
                    )
                )
            ).all()
        )
        if rows
        else {}
    )
    out = []
    for order, custom in rows:
        day, at = delivery_date_parts(order)
        out.append(
            CustomOrderListItem(
                id=order.id,
                order_number=order.order_number,
                status=order.status.value,
                delivery_date=day,
                delivery_time=at,
                customer_name=order.customer_name,
                summary=titles.get(order.id, ""),
                total=to_decimal(order.total),
                kitchen_printed_at=custom.kitchen_printed_at,
                delivery_provider=providers.get(order.id),
            )
        )
    return out


def cake_item_out(item: InventoryItem, on_hand: Decimal) -> CustomCakeItem:
    return CustomCakeItem(
        id=item.id,
        sku=item.sku,
        name=item.name,
        unit=item.ingredient_unit,
        on_hand=item.to_ingredient_units(on_hand),
        production_unit=item.storage_unit,
        on_hand_in_production_unit=on_hand,
    )
