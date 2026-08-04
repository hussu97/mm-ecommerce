from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Integer, cast, func, select, update as sql_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.branch import Branch
from app.models.cart import Cart, CartItem
from app.models.order import DeliveryMethodEnum, Order, OrderItem, OrderStatusEnum
from app.models.product import Product
from app.models.promo_code import PromoCode
from app.schemas.order import OrderCreate, OrderListResponse, OrderResponse
from app.services import (
    batching_service,
    cart_service,
    courier_service,
    delivery_service,
    delivery_zone_service,
    lalamove_service,
    pos_order_service,
    promo_code_service,
)
from app.services.storefront_visibility import is_website_product_visible
from app.services.delivery_zone_service import Zone

logger = logging.getLogger(__name__)

__all__ = [
    "VALID_TRANSITIONS",
    "create_order",
    "get_all_admin",
    "get_by_order_number",
    "get_user_orders",
    "update_status",
]


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Valid status transitions
VALID_TRANSITIONS: dict[OrderStatusEnum, set[OrderStatusEnum]] = {
    OrderStatusEnum.CREATED: {
        OrderStatusEnum.CONFIRMED,
        OrderStatusEnum.CANCELLED,
        OrderStatusEnum.PAYMENT_FAILED,
    },
    OrderStatusEnum.PAYMENT_FAILED: {
        OrderStatusEnum.CANCELLED,
        OrderStatusEnum.CONFIRMED,
    },
    OrderStatusEnum.CONFIRMED: {
        OrderStatusEnum.PACKED,
        OrderStatusEnum.CANCELLED,
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.DISPUTED,
    },
    OrderStatusEnum.PACKED: {
        OrderStatusEnum.OUT_FOR_DELIVERY,
        # A third-party zone has no courier reporting back, so the shop marks
        # the order delivered itself and never passes through the middle state.
        OrderStatusEnum.DELIVERED,
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.DISPUTED,
    },
    # A courier that rejects, expires or cancels a booking does not cancel the
    # order — it is paid for and boxed. The delivery row records the failure and
    # an admin re-dispatches or refunds, which is why cancelling is still not
    # reachable from here.
    OrderStatusEnum.OUT_FOR_DELIVERY: {
        OrderStatusEnum.DELIVERED,
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.DISPUTED,
    },
    OrderStatusEnum.DELIVERED: {
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.DISPUTED,
    },
    OrderStatusEnum.CANCELLED: set(),
    # Terminal states — set by Stripe webhooks only, no manual transitions out
    OrderStatusEnum.REFUNDED: set(),
    OrderStatusEnum.DISPUTED: set(),
}


def _order_load_options():
    return [selectinload(Order.items)]


async def _generate_order_number(db: AsyncSession) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"MM-{today}-"

    # Max of the numeric sequence, not a string sort — once the day passes 999
    # orders, "MM-...-1000" sorts below "MM-...-999" lexicographically and a
    # string max would hand out a duplicate number.
    result = await db.execute(
        select(
            func.max(cast(func.split_part(Order.order_number, "-", 3), Integer))
        ).where(Order.order_number.like(f"{prefix}%"))
    )
    last_seq = result.scalar_one_or_none()

    return f"{prefix}{int(last_seq or 0) + 1:03d}"


# ── Private helpers for create_order ──────────────────────────────────────────


async def _locate_cart(
    db: AsyncSession,
    user_id: uuid.UUID | None,
    session_id: str | None,
) -> Cart:
    """
    Find the active cart for this user or session.

    Priority: user_id cart → session_id cart (fallback).

    The fallback matters for the guest-checkout flow: the frontend creates a
    guest JWT just before submitting the order, so ``user_id`` is set in the
    token but the cart was built using ``session_id``.  Without the fallback
    the lookup would return nothing and raise "Cart is empty".
    """
    if not user_id and not session_id:
        raise BadRequestError("session_id is required for guest checkout")

    cart: Cart | None = None

    if user_id:
        result = await db.execute(
            select(Cart)
            .options(
                selectinload(Cart.items)
                .joinedload(CartItem.product)
                .joinedload(Product.category)
            )
            .where(Cart.user_id == user_id)
        )
        cart = result.scalar_one_or_none()

    # Fallback to session-id cart (guest user who just received a JWT)
    if (not cart or not cart.items) and session_id:
        result = await db.execute(
            select(Cart)
            .options(
                selectinload(Cart.items)
                .joinedload(CartItem.product)
                .joinedload(Product.category)
            )
            .where(Cart.session_id == session_id)
        )
        session_cart = result.scalar_one_or_none()
        if session_cart and session_cart.items:
            cart = session_cart

    if not cart or not cart.items:
        raise BadRequestError("Cart is empty")

    return cart


def _compute_item_totals(cart: Cart) -> tuple[list[dict], Decimal]:
    """
    Validate cart items and compute the order subtotal.

    Returns (items_data, subtotal).
    items_data is the list of dicts used to create OrderItem rows.
    """
    subtotal = Decimal("0.00")
    items_data: list[dict] = []

    for cart_item in cart.items:
        product = cart_item.product
        if not is_website_product_visible(product):
            raise BadRequestError("A product in your cart is no longer available")

        selected_options = cart_item.selected_options or []
        # Shared with the cart's own pricing so a checkout can never total a
        # basket differently from the basket page the shopper just read.
        options_price = sum(
            (cart_service.option_charge(opt) for opt in selected_options),
            Decimal("0"),
        )
        base_price = Decimal(str(product.base_price))
        unit_price = base_price + options_price
        line_total = unit_price * cart_item.quantity
        subtotal += line_total

        items_data.append(
            {
                "product_id": product.id,
                "product_name": product.name,
                "product_sku": product.sku or "",
                "product_translations": product.translations or {},
                "quantity": cart_item.quantity,
                "base_price": base_price,
                "options_price": options_price,
                "unit_price": unit_price,
                "total_price": line_total,
                "selected_options_snapshot": selected_options,
            }
        )

    return items_data, subtotal


async def _decrement_stock(db: AsyncSession, cart: Cart) -> None:
    """
    Atomically claim stock for stock-tracked products.

    The guarded UPDATE decrements and enforces availability in one statement,
    so two concurrent checkouts cannot both take the last item. Runs in the
    order-creation transaction: if the order fails, the claim rolls back.
    """
    quantities: dict[uuid.UUID, tuple[str, int]] = {}
    for cart_item in cart.items:
        product = cart_item.product
        if product and product.is_stock_product:
            name, qty = quantities.get(product.id, (product.name, 0))
            quantities[product.id] = (name, qty + cart_item.quantity)

    for product_id, (name, qty) in quantities.items():
        result = await db.execute(
            sql_update(Product)
            .where(Product.id == product_id, Product.stock_quantity >= qty)
            .values(stock_quantity=Product.stock_quantity - qty)
            .returning(Product.id)
            .execution_options(synchronize_session=False)
        )
        if not result.scalar_one_or_none():
            raise BadRequestError(f"Product '{name}' is out of stock")


async def _compute_order_totals(
    data: OrderCreate,
    subtotal: Decimal,
    discount_amount: Decimal,
    db: AsyncSession,
    user_id: uuid.UUID | None = None,
    email: str | None = None,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Zone | None]:
    """
    Compute delivery fee, order total, and VAT figures.

    Returns (delivery_fee, total, vat_amount, total_excl_vat, zone). The zone
    comes back because it also decides who carries the order, and resolving it
    twice risks the two answers disagreeing if the map is published in between.
    """
    discounted_subtotal = subtotal - discount_amount
    address = data.shipping_address
    zone = (
        await delivery_zone_service.find_zone(
            db, float(address.latitude), float(address.longitude)
        )
        if address is not None
        and address.latitude is not None
        and address.longitude is not None
        else None
    )
    # The fee is priced off the pin, and only the pin — with one exception, the
    # trial accounts, who pay nothing. Their identity has to reach this call or
    # the checkout would show them free delivery and the order would charge for
    # it anyway.
    delivery_fee = await delivery_service.calculate_fee(
        data.delivery_method,
        discounted_subtotal,
        db,
        latitude=address.latitude if address else None,
        longitude=address.longitude if address else None,
        user_id=user_id,
        email=email,
    )
    total = discounted_subtotal + delivery_fee

    # VAT back-calculation (goods only; delivery excluded per UAE VAT rules)
    VAT_RATE = Decimal("0.05")
    taxable = subtotal - discount_amount
    vat_amount = (taxable * VAT_RATE / (1 + VAT_RATE)).quantize(Decimal("0.01"))
    total_excl_vat = (taxable / (1 + VAT_RATE)).quantize(Decimal("0.01"))

    return delivery_fee, total, vat_amount, total_excl_vat, zone


def _is_cash_on_delivery(data: OrderCreate) -> bool:
    return str(getattr(data.payment_method, "value", data.payment_method)) == "cod"


async def _attach_to_branch(db: AsyncSession, order: Order, zone: Zone | None) -> None:
    """
    Hand the order to the kitchen that serves its zone.

    Best-effort by design. A zone with no branch, a branch that has been
    deleted, or a pin outside every zone all leave the order exactly as it was —
    visible in the admin, dispatchable, simply not on a register. Refusing the
    sale because a kitchen is misconfigured would be a far worse trade, and the
    orders this misses are findable by `branch_id IS NULL`.
    """
    branch_id = zone.branch_id if zone else None
    if branch_id is None:
        return

    branch = await db.get(Branch, branch_id)
    if branch is None or branch.deleted_at is not None or not branch.is_active:
        logger.warning(
            "Order %s is in zone %s, whose branch %s cannot take it",
            order.order_number,
            zone.name if zone else "-",
            branch_id,
        )
        return

    await pos_order_service.attach_online_order(db, order, branch)


async def _persist_order(
    db: AsyncSession,
    data: OrderCreate,
    user_id: uuid.UUID | None,
    cart: Cart,
    items_data: list[dict],
    subtotal: Decimal,
    discount_amount: Decimal,
    promo_code_used: str | None,
    promo_obj: PromoCode | None,
    delivery_fee: Decimal,
    total: Decimal,
    vat_amount: Decimal,
    total_excl_vat: Decimal,
    fallback_email: str | None = None,
) -> Order:
    """
    Write the order, its items, and update promo usage atomically.
    Clears the cart on success and returns the persisted order.
    """
    VAT_RATE = Decimal("0.05")

    # `orders.email` is not nullable and is what every downstream lookup keys
    # on. When the customer declines to give one we fall back to the session's
    # own address, which for a guest is the generated `…@guest.local` the auth
    # layer already mints — a value the mailer knows not to write to.
    order_email = data.email or fallback_email
    if not order_email:
        raise BadRequestError("An email address or an active session is required")

    address_snapshot: dict | None = (
        data.shipping_address.model_dump(mode="json") if data.shipping_address else None
    )

    order = Order(
        order_number=await _generate_order_number(db),
        user_id=user_id,
        email=order_email,
        delivery_method=data.delivery_method,
        delivery_fee=delivery_fee,
        subtotal=subtotal,
        discount_amount=discount_amount,
        total=total,
        vat_rate=VAT_RATE,
        vat_amount=vat_amount,
        total_excl_vat=total_excl_vat,
        status=OrderStatusEnum.CREATED,
        promo_code_used=promo_code_used,
        shipping_address_snapshot=address_snapshot,
        payment_method=data.payment_method,
        notes=data.notes,
    )
    # Two checkouts can read the same max sequence and generate the same
    # number; the unique constraint catches the loser, who regenerates and
    # retries inside a savepoint so the rest of the transaction survives.
    for attempt in range(3):
        try:
            async with db.begin_nested():
                db.add(order)
                await db.flush()
            break
        except IntegrityError:
            if attempt == 2:
                raise
            order.order_number = await _generate_order_number(db)

    for item in items_data:
        db.add(OrderItem(order_id=order.id, **item))

    # Atomic promo-use increment
    if promo_obj:
        if promo_obj.max_uses is not None:
            result = await db.execute(
                sql_update(PromoCode)
                .where(
                    PromoCode.id == promo_obj.id,
                    PromoCode.current_uses < PromoCode.max_uses,
                )
                .values(current_uses=PromoCode.current_uses + 1)
                .returning(PromoCode.id)
                .execution_options(synchronize_session=False)
            )
            if not result.scalar_one_or_none():
                raise BadRequestError("Promo code has reached its usage limit")
        else:
            await db.execute(
                sql_update(PromoCode)
                .where(PromoCode.id == promo_obj.id)
                .values(current_uses=PromoCode.current_uses + 1)
                .execution_options(synchronize_session=False)
            )

    # Clear cart
    items_result = await db.execute(select(CartItem).where(CartItem.cart_id == cart.id))
    for ci in items_result.scalars().all():
        await db.delete(ci)

    await db.flush()
    return order


# ── Public API ────────────────────────────────────────────────────────────────


async def create_order(
    db: AsyncSession,
    data: OrderCreate,
    user_id: uuid.UUID | None,
    fallback_email: str | None = None,
) -> OrderResponse:
    # 1. Locate and validate cart (with session_id fallback for guest checkout)
    cart = await _locate_cart(db, user_id, data.session_id)

    if (
        data.delivery_method == DeliveryMethodEnum.DELIVERY
        and not data.shipping_address
    ):
        raise BadRequestError("Shipping address is required for delivery orders")

    # 2. Compute item subtotals
    items_data, subtotal = _compute_item_totals(cart)

    # 3. Apply promo code
    discount_amount = Decimal("0.00")
    promo_code_used: str | None = None
    promo_obj: PromoCode | None = None

    if data.promo_code:
        validation = await promo_code_service.validate(db, data.promo_code, subtotal)
        if not validation.valid:
            raise BadRequestError(f"Promo code: {validation.message}")
        discount_amount = validation.discount_amount
        promo_code_used = data.promo_code.upper()
        promo_obj = await promo_code_service.get_promo(db, data.promo_code)

    # 4. Compute delivery fee, total, VAT
    (
        delivery_fee,
        total,
        vat_amount,
        total_excl_vat,
        zone,
    ) = await _compute_order_totals(
        data,
        subtotal,
        discount_amount,
        db,
        user_id=user_id,
        # The same address `_persist_order` will stamp on the order, resolved
        # the same way, so the fee and the identity it was priced for cannot
        # disagree.
        email=data.email or fallback_email,
    )

    # 5. Claim stock for stock-tracked products (fails if any is out of stock)
    await _decrement_stock(db, cart)

    # 6. Persist order rows and clear cart
    order = await _persist_order(
        db,
        data,
        user_id,
        cart,
        items_data,
        subtotal,
        discount_amount,
        promo_code_used,
        promo_obj,
        delivery_fee,
        total,
        vat_amount,
        total_excl_vat,
        fallback_email,
    )

    # 7. Open the delivery record — including for zones no courier API touches,
    #    so "what did fulfilment cost" is answerable for the whole country and
    #    not just the automated part of it.
    if data.delivery_method == DeliveryMethodEnum.DELIVERY:
        await lalamove_service.record_order_delivery(db, order, zone=zone, cart=cart)

    # 8. Put it on a register. The zone that priced it names the kitchen that
    #    bakes it; without this the order exists only in the admin and nobody at
    #    the counter is told anything.
    await _attach_to_branch(db, order, zone)

    # 9. Cash orders confirm themselves. A card order is confirmed by Stripe's
    #    `payment_intent.succeeded` and a failure lands it in `payment_failed`,
    #    but cash has no such event — so without this it would sit in `created`
    #    until an admin noticed, which for an order already printing in a
    #    kitchen is a status that means nothing.
    if _is_cash_on_delivery(data) and order.status == OrderStatusEnum.CREATED:
        order.status = OrderStatusEnum.CONFIRMED

    stmt = select(Order).options(*_order_load_options()).where(Order.id == order.id)
    result = await db.execute(stmt)
    return OrderResponse.model_validate(result.scalar_one())


async def get_user_orders(
    db: AsyncSession,
    user_id: uuid.UUID,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[OrderListResponse], int]:
    base_stmt = select(Order).where(Order.user_id == user_id)

    count_result = await db.execute(
        select(func.count()).select_from(base_stmt.subquery())
    )
    total = count_result.scalar() or 0

    stmt = (
        base_stmt.options(selectinload(Order.items))
        .order_by(Order.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    result = await db.execute(stmt)
    orders = result.scalars().all()

    items = []
    for o in orders:
        resp = OrderListResponse.model_validate(o)
        resp.item_count = sum(i.quantity for i in o.items)
        items.append(resp)
    return items, total


async def get_by_order_number(
    db: AsyncSession,
    order_number: str,
    user_id: uuid.UUID | None = None,
    admin: bool = False,
    email: str | None = None,
) -> OrderResponse:
    stmt = (
        select(Order)
        .options(*_order_load_options())
        .where(Order.order_number == order_number)
    )
    result = await db.execute(stmt)
    order = result.scalar_one_or_none()
    if not order:
        raise NotFoundError(f"Order '{order_number}' not found")

    if not admin:
        if user_id:
            if order.user_id != user_id:
                raise ForbiddenError("Not your order")
        elif not email or order.email != email.lower().strip():
            # An order number alone must never expose the full order — it
            # carries the customer's email and address. Unauthenticated callers
            # prove ownership with the order's email, like /orders/track.
            raise ForbiddenError("Not your order")

    return OrderResponse.model_validate(order)


async def update_status(
    db: AsyncSession,
    order_number: str,
    new_status: OrderStatusEnum,
    admin_notes: str | None = None,
) -> OrderResponse:
    stmt = (
        select(Order)
        .options(*_order_load_options())
        .where(Order.order_number == order_number)
    )
    result = await db.execute(stmt)
    order = result.scalar_one_or_none()
    if not order:
        raise NotFoundError(f"Order '{order_number}' not found")

    allowed = VALID_TRANSITIONS.get(order.status, set())
    if new_status not in allowed:
        raise BadRequestError(
            f"Cannot transition order from '{order.status}' to '{new_status}'. "
            f"Allowed: {[s.value for s in allowed] or 'none (terminal state)'}"
        )

    order.status = new_status
    if admin_notes is not None:
        order.admin_notes = admin_notes

    # Packed means the box is ready, which is the moment it can travel —
    # earlier and a driver waits at the counter. Whether it leaves now or waits
    # for the rest of its batch is the zone's schedule to decide. Nothing
    # happens for a third-party zone: the call returns the record untouched and
    # the flow stays the manual one it has always been.
    if new_status == OrderStatusEnum.PACKED:
        await batching_service.assign_or_dispatch(db, order)
    elif new_status == OrderStatusEnum.CANCELLED:
        delivery = await lalamove_service.get_delivery(db, order.id)
        if delivery is not None:
            # Off the run first, so a batch that is now empty does not go out
            # to collect nothing.
            await batching_service.cancel_assignment(db, delivery)
        await courier_service.cancel(db, order)

    # A cancelled order releases the stock it claimed at creation.
    if new_status == OrderStatusEnum.CANCELLED:
        for item in order.items:
            if item.product_id:
                await db.execute(
                    sql_update(Product)
                    .where(
                        Product.id == item.product_id,
                        Product.is_stock_product.is_(True),
                    )
                    .values(stock_quantity=Product.stock_quantity + item.quantity)
                    .execution_options(synchronize_session=False)
                )

    await db.flush()
    await db.refresh(order)

    stmt = select(Order).options(*_order_load_options()).where(Order.id == order.id)
    result = await db.execute(stmt)
    order = result.scalar_one()
    return OrderResponse.model_validate(order)


async def get_all_admin(
    db: AsyncSession,
    status: OrderStatusEnum | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[OrderListResponse], int]:
    base_stmt = select(Order)

    if status:
        base_stmt = base_stmt.where(Order.status == status)
    if search:
        escaped = _escape_like(search)
        base_stmt = base_stmt.where(
            Order.order_number.ilike(f"%{escaped}%", escape="\\")
            | Order.email.ilike(f"%{escaped}%", escape="\\")
        )

    count_result = await db.execute(
        select(func.count()).select_from(base_stmt.subquery())
    )
    total = count_result.scalar() or 0

    stmt = (
        base_stmt.options(selectinload(Order.items))
        .order_by(Order.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    result = await db.execute(stmt)
    orders = result.scalars().all()

    items = []
    for o in orders:
        resp = OrderListResponse.model_validate(o)
        resp.item_count = sum(i.quantity for i in o.items)
        items.append(resp)
    return items, total
