from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import (
    Integer,
    cast,
    func,
    inspect as sa_inspect,
    select,
    update as sql_update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload, selectinload

from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.branch import Branch
from app.models.cart import Cart, CartItem
from app.models.delivery_batch import DELIVERY_TIMEZONE
from app.models.delivery_settings import DeliverySettings
from app.models.order import DeliveryMethodEnum, Order, OrderItem, OrderStatusEnum
from app.models.product import Product
from app.models.pos_order import OrderSourceEnum, PosOrderStatusEnum
from app.models.promo_code import PromoCode
from app.models.user import User
from app.schemas.fulfilment import FulfilmentResponse
from app.schemas.order import OrderCreate, OrderListResponse, OrderResponse
from app.services import (
    batching_service,
    cart_service,
    courier_service,
    delivery_service,
    email_service,
    fulfilment_service,
    lalamove_service,
    pos_order_service,
    pos_pricing,
    promo_code_service,
    push_service,
)
from app.services.storefront_visibility import is_website_product_visible
from app.services.delivery_zone_service import Zone

logger = logging.getLogger(__name__)

__all__ = [
    "SUPPORTED_LOCALES",
    "VALID_TRANSITIONS",
    "normalise_locale",
    "create_order",
    "to_response",
    "get_all_admin",
    "get_by_order_number",
    "get_for_notification",
    "get_user_orders",
    "update_status",
]


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


#: UAE standard rate, as a fraction. Declared once: it used to be written out
#: separately in `_vat_of` and in the order-persisting path, so a rate change
#: would have had to be found twice.
VAT_RATE = Decimal("0.05")


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
        # A driver can also fail to hand it over on a run we never saw start.
        OrderStatusEnum.UNDELIVERED,
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.DISPUTED,
    },
    # A courier that rejects, expires or cancels a booking does not cancel the
    # order — it is paid for and boxed. The delivery row records the failure and
    # an admin re-dispatches or refunds, which is why cancelling is still not
    # reachable from here.
    OrderStatusEnum.OUT_FOR_DELIVERY: {
        OrderStatusEnum.DELIVERED,
        OrderStatusEnum.UNDELIVERED,
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.DISPUTED,
    },
    # A failed handover is a detour, not an ending. The cake exists, it is paid
    # for, and the usual answer is a second attempt — which is why this leads
    # back into the journey rather than only out of it. `packed` is the way
    # back: a re-dispatch starts from a box on a shelf, exactly as the first
    # one did, and `OUT_FOR_DELIVERY` follows when the new rider collects.
    OrderStatusEnum.UNDELIVERED: {
        OrderStatusEnum.PACKED,
        OrderStatusEnum.OUT_FOR_DELIVERY,
        OrderStatusEnum.DELIVERED,
        OrderStatusEnum.CANCELLED,
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


async def to_response(db: AsyncSession, order: Order) -> OrderResponse:
    """
    The order as its customer sees it, fulfilment included.

    One function rather than a `model_validate` at each call site, because the
    fulfilment block is the part every reader needs and the part every reader
    would otherwise forget: the confirmation email, the account page, the track
    page and the order returned from checkout all have to agree about when the
    box arrives, and they only do that by construction.
    """
    await _ensure_items_loaded(db, order)
    response = OrderResponse.model_validate(order)
    response.email_has_account = await _email_has_account(db, order.email)
    response.fulfilment = FulfilmentResponse.of(
        await fulfilment_service.for_order(db, order)
    )
    return response


async def _ensure_items_loaded(db: AsyncSession, order: Order) -> None:
    """
    Load `order.items` if whoever handed us this row did not.

    `OrderResponse` has an `items` field, so validating an order whose items are
    still unloaded lazy-loads them — and a lazy load inside async SQLAlchemy is
    not a slow query, it is a `MissingGreenlet`. Every caller that selects with
    `_order_load_options()` is already fine; the ones that reach here with a bare
    `select(Order)` are the courier webhooks, and on 2026-08-05 that cost four
    customer emails: `out_for_delivery` and `delivered` for MM-20260805-007, and
    `out_for_delivery` and `undelivered` for -006. `notify_order` catches
    everything, so the failure wrote no email log and left no trace at all.

    Fixed at the choke point rather than at each call site, because the next
    caller to select an order without options will not know it had to.
    """
    state = sa_inspect(order)
    # A row that was never persisted — the POS path builds one by hand — has no
    # identity to refresh against, and its `items` collection is already real.
    if state.transient or state.pending:
        return
    if "items" in state.unloaded:
        await db.refresh(order, ["items"])


async def get_for_notification(db: AsyncSession, order_id: uuid.UUID) -> Order | None:
    """
    Re-read an order with everything an email needs already loaded.

    For callers holding an order they selected for a different purpose — both
    couriers select one to move its status and then want it written about. One
    extra query, against a mailer that must never fail because a relationship
    was not asked for.
    """
    return (
        (
            await db.execute(
                select(Order)
                .options(*_order_load_options())
                .where(Order.id == order_id)
            )
        )
        .scalars()
        .first()
    )


async def _generate_order_number(db: AsyncSession) -> str:
    # The shop's day, not UTC's. Dubai is UTC+4, so a UTC date rolled the
    # counter over at 04:00 local: an order taken at 01:00 Tuesday morning was
    # stamped Monday, and one taken at 05:00 Tuesday was stamped Tuesday, which
    # made the number disagree with the `business_date` on the same row and with
    # every report built from it.
    today = datetime.now(ZoneInfo(DELIVERY_TIMEZONE)).strftime("%Y%m%d")
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


@dataclass(frozen=True)
class OrderTotals:
    """
    Every figure the order row needs, named.

    This used to be a five-tuple unpacked positionally at its one call site,
    which was survivable while it held one fee. It now holds two, both `Decimal`,
    both money, and adjacent — and a tuple whose second and third elements can be
    swapped without a type error is how a small-basket fee ends up charged to the
    customer as delivery, reconciled against the courier's bill, and quietly
    wrecking the freight margin report. Naming them costs nothing and removes the
    whole class of mistake.
    """

    delivery_fee: Decimal
    #: The small-basket surcharge, zero on everything above the threshold and on
    #: every pickup order.
    low_order_fee: Decimal
    total: Decimal
    vat_amount: Decimal
    total_excl_vat: Decimal
    #: Comes back because it also decides who carries the order, and resolving it
    #: twice risks the two answers disagreeing if the map is published in between.
    zone: Zone | None


def low_order_fee_for(
    subtotal: Decimal,
    delivery_method: DeliveryMethodEnum,
    settings: DeliverySettings,
) -> Decimal:
    """
    The small-basket surcharge, if this order attracts one.

    Delivery only. A pickup order costs us nothing to hand over, so charging for
    a small one would be a fee with no cost behind it.

    **Judged on the basket before any discount.** Free delivery is judged on the
    discounted figure, and the asymmetry is deliberate rather than an oversight:
    free delivery is a reward, and rewarding someone for what they actually paid
    is right; this is a surcharge, and a surcharge that appears *because* the
    customer applied a coupon is indefensible. On the gross figure a 40-dirham
    basket with a 15% new-customer code stays a 40-dirham basket and attracts
    nothing. On the discounted figure it would fall to 34, trip the threshold,
    and hand back a 15-dirham fee against a 6-dirham discount — the acquisition
    offer fighting itself.
    """
    if delivery_method == DeliveryMethodEnum.PICKUP:
        return Decimal("0.00")
    threshold = settings.low_order_threshold
    if threshold is None or subtotal > threshold:
        return Decimal("0.00")
    return settings.low_order_fee or Decimal("0.00")


async def _compute_order_totals(
    data: OrderCreate,
    subtotal: Decimal,
    discount_amount: Decimal,
    db: AsyncSession,
    user_id: uuid.UUID | None = None,
    email: str | None = None,
) -> OrderTotals:
    """
    Compute delivery fee, small-basket fee, order total, and VAT figures.

    Raises `UnserviceableAreaError` when the pin lands somewhere nothing can be
    priced to. That is a refusal to take the money, deliberately, at the last
    moment it can still be refused cleanly.
    """
    discounted_subtotal = subtotal - discount_amount
    address = data.shipping_address
    settings = await delivery_service.get_settings(db)
    low_order_fee = low_order_fee_for(subtotal, data.delivery_method, settings)

    if data.delivery_method == DeliveryMethodEnum.PICKUP:
        vat_amount, total_excl_vat = _vat_of(discounted_subtotal)
        return OrderTotals(
            delivery_fee=settings.pickup_fee,
            low_order_fee=low_order_fee,
            total=discounted_subtotal + settings.pickup_fee,
            vat_amount=vat_amount,
            total_excl_vat=total_excl_vat,
            zone=None,
        )

    # The fee is priced off the pin, and only the pin. One call, so the zone the
    # order is filed against is the same zone its price came from.
    priced = await delivery_service.price(
        db,
        discounted_subtotal,
        latitude=address.latitude if address else None,
        longitude=address.longitude if address else None,
        address=address.address_line_1 if address else None,
        settings=settings,
    )
    if not priced.serviceable:
        raise delivery_service.UnserviceableAreaError()

    delivery_fee = settings.default_delivery_fee if priced.fee is None else priced.fee

    # VAT is on goods only. Both fees sit outside it, for the same reason
    # delivery always has — see `_vat_of`.
    vat_amount, total_excl_vat = _vat_of(discounted_subtotal)
    return OrderTotals(
        delivery_fee=delivery_fee,
        low_order_fee=low_order_fee,
        total=discounted_subtotal + delivery_fee + low_order_fee,
        vat_amount=vat_amount,
        total_excl_vat=total_excl_vat,
        zone=priced.zone,
    )


def _vat_of(taxable: Decimal) -> tuple[Decimal, Decimal]:
    """
    VAT back-calculation (goods only; both fees excluded per UAE VAT rules).

    The small-basket fee is treated exactly as delivery is — outside the VAT
    base — so the two service charges on an order are handled the same way.

    Delegates to the register's splitter so a website order and a counter order
    do the same arithmetic. Two things change by doing so: the rounding is
    ROUND_HALF_UP rather than Python's default banker's rounding — half a fils
    on a tax figure should go up, the way the printed receipt shows it — and
    the two halves are guaranteed to add back to `taxable`, because the tax is
    the remainder after the net rather than a second independent rounding.
    """
    net, vat = pos_pricing.split_inclusive_tax(taxable, VAT_RATE)
    return (
        vat,
        net,
    )


#: Register states a cancellation still has to close. `closed`, `void`,
#: `joined` and the rest are already finished, and a cancellation arriving after
#: them must not reopen or relabel what the till already settled.
_OPEN_ON_THE_REGISTER = frozenset(
    {
        PosOrderStatusEnum.DRAFT.value,
        PosOrderStatusEnum.PENDING.value,
        PosOrderStatusEnum.ACTIVE.value,
    }
)


#: The languages the shop writes in. Anything else becomes English.
SUPPORTED_LOCALES = frozenset({"en", "ar"})
DEFAULT_LOCALE = "en"


def normalise_locale(value: str | None) -> str:
    """
    The language to write to this customer in.

    Takes whatever the browser sent — a bare code, a regional tag like `ar-AE`,
    a stray capital, or nothing at all — and answers with one of the two the
    shop actually has copy for. Never raises: a locale we do not recognise is a
    reason to write in English, not a reason to refuse a paid order.
    """
    if not value:
        return DEFAULT_LOCALE
    code = str(value).strip().lower().replace("_", "-").split("-")[0]
    return code if code in SUPPORTED_LOCALES else DEFAULT_LOCALE


def _is_cash_on_delivery(data: OrderCreate) -> bool:
    return str(getattr(data.payment_method, "value", data.payment_method)) == "cod"


async def resolve_branch(
    db: AsyncSession,
    zone: Zone | None,
    *,
    pickup_branch_id: uuid.UUID | None = None,
) -> Branch | None:
    """
    The kitchen that will make this order.

    A pickup order's chosen branch wins over everything, because for that order
    it is not a guess at all — the customer said where they are going, and the
    place they are driving to has to be the place the box is waiting. It is
    validated rather than trusted: an id naming a branch that does not offer
    collection falls through to the same resolution as any other order, so a
    stale or hand-edited request cannot send somebody to a warehouse.

    Otherwise the zone's own branch first — that is the whole point of mapping
    polygons to branches. Failing that, the configured pickup branch, which is
    where a courier would collect from anyway: a zone drawn before branches
    existed, a pickup order with no zone at all, or a pin outside every drawn
    shape still has to be baked *somewhere*, and the shop that would have made
    it is the shop that should see it.

    Resolved **before** the order is written, because `orders.branch_id` is NOT
    NULL — an order with no kitchen is not a quieter order, it is an order
    nobody is making. `None` here means the shop has no branch that can take
    online orders at all, which is a configuration failure the caller turns into
    a refusal rather than a silent sale.
    """
    if pickup_branch_id is not None:
        chosen = next(
            (
                branch
                for branch in await fulfilment_service.pickup_branches(db)
                if branch.id == pickup_branch_id
            ),
            None,
        )
        if chosen is not None:
            return chosen
        logger.warning(
            "Order names pickup branch %s, which does not offer collection",
            pickup_branch_id,
        )

    if zone is not None and zone.branch_id is not None:
        branch = await db.get(Branch, zone.branch_id)
        if branch is not None and branch.deleted_at is None and branch.is_active:
            return branch
        logger.warning(
            "Zone %s names branch %s, which cannot take orders",
            zone.name,
            zone.branch_id,
        )

    pickup = await lalamove_service.resolve_pickup(db)
    if pickup is not None:
        branch = (
            (
                await db.execute(
                    select(Branch).where(Branch.reference == pickup.reference)
                )
            )
            .scalars()
            .first()
        )
        if branch is not None:
            return branch

    # Last resort: any branch that is open for business. `resolve_pickup` is
    # stricter than this on purpose — it needs coordinates and a phone number,
    # because a courier has to be sent somewhere and someone has to answer — but
    # a kitchen missing its pin can still bake a cake and print a ticket. Losing
    # the sale over a field nobody filled in would be the worse trade by far.
    return (
        (
            await db.execute(
                select(Branch)
                .where(Branch.is_active.is_(True), Branch.deleted_at.is_(None))
                .order_by(Branch.display_order, Branch.name)
            )
        )
        .scalars()
        .first()
    )


async def _attach_to_branch(db: AsyncSession, order: Order, branch: Branch) -> None:
    """
    Give the order its place on the register: check number, business day, state.

    Separate from `resolve_branch` and deliberately best-effort. Which kitchen
    is making it is part of the order and is written with it; whether the till
    could open a business day for it is not worth losing a paid sale over. What
    this misses is findable by `is_pos = false AND source = 'online'`.
    """
    try:
        await pos_order_service.attach_online_order(db, order, branch)
    except Exception:  # pragma: no cover — defensive
        logger.exception(
            "Order %s could not be put on %s's register",
            order.order_number,
            branch.reference,
        )


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
    branch: Branch | None = None,
    promised: delivery_service.DeliveryEstimate | None = None,
    low_order_fee: Decimal = Decimal("0.00"),
) -> Order:
    """
    Write the order, its items, and update promo usage atomically.
    Clears the cart on success and returns the persisted order.
    """
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
        locale=normalise_locale(data.locale),
        delivery_method=data.delivery_method,
        delivery_fee=delivery_fee,
        low_order_fee=low_order_fee,
        subtotal=subtotal,
        discount_amount=discount_amount,
        total=total,
        vat_rate=VAT_RATE,
        vat_amount=vat_amount,
        total_excl_vat=total_excl_vat,
        status=OrderStatusEnum.CREATED,
        # Which channel rang this up, stamped at creation and not later.
        # `attach_online_order` used to be the only thing that set it, which
        # meant an order in a zone with no branch — or a pickup outside every
        # zone — was written with no channel at all, and every reader had to
        # treat null as "probably the website".
        source=OrderSourceEnum.ONLINE.value,
        # The kitchen, stamped with the row rather than onto it afterwards. The
        # column is NOT NULL: an order nobody is making is not a state worth
        # being able to represent.
        branch_id=branch.id if branch is not None else None,
        promo_code_used=promo_code_used,
        shipping_address_snapshot=address_snapshot,
        payment_method=data.payment_method,
        notes=data.notes,
        # What the checkout said, kept verbatim. Every email about this order
        # repeats this rather than working out its own answer — which is how the
        # confirmation for MM-20260805-008 promised 17:25 against a checkout
        # that had said 19:00.
        promised_at=promised.at if promised else None,
        promised_precision=promised.precision if promised else None,
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
        validation = await promo_code_service.validate(
            db, data.promo_code, subtotal, user_id=user_id
        )
        if not validation.valid:
            raise BadRequestError(f"Promo code: {validation.message}")
        discount_amount = validation.discount_amount
        promo_code_used = data.promo_code.upper()
        promo_obj = await promo_code_service.get_promo(db, data.promo_code)

    # 4. Compute delivery fee, small-basket fee, total, VAT
    totals = await _compute_order_totals(
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

    # 5. Find the kitchen before writing anything. `orders.branch_id` is NOT
    #    NULL, so this is part of building the row rather than something done to
    #    it afterwards — and a shop with no branch that can take online orders
    #    is a shop that cannot take this one.
    branch = await resolve_branch(
        db,
        totals.zone,
        pickup_branch_id=(
            data.pickup_branch_id
            if data.delivery_method == DeliveryMethodEnum.PICKUP
            else None
        ),
    )
    if branch is None:
        logger.error("No branch can take online orders; refusing %s", data.email)
        raise BadRequestError(
            "We can't take online orders right now. Please try again shortly."
        )

    # 5b. What the checkout told this customer, captured before the row is
    #     written so the order carries the promise rather than a later
    #     re-derivation of it.
    #
    #     Delivery only. A collection order's estimate is `created_at + prep`,
    #     which is deterministic and cannot drift, so there is nothing a stored
    #     copy would protect. A delivery's estimate reads the batch window open
    #     *now*, and "now" is different by the time any email is sent.
    #
    #     Asked here rather than taken from the request: the browser is not the
    #     record of what we promised, and a client that sent a flattering number
    #     would be believed.
    promised: delivery_service.DeliveryEstimate | None = None
    if data.delivery_method == DeliveryMethodEnum.DELIVERY:
        promised = await delivery_service.estimate_arrival(db, totals.zone)

    # 6. Claim stock for stock-tracked products (fails if any is out of stock)
    await _decrement_stock(db, cart)

    # 7. Persist order rows and clear cart
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
        totals.delivery_fee,
        totals.total,
        totals.vat_amount,
        totals.total_excl_vat,
        fallback_email,
        branch,
        promised,
        # Keyword, not positional. It is a `Decimal` sitting next to three other
        # `Decimal`s and the argument list is already thirteen long — the one
        # place a silent swap could still happen is the call, so it is named.
        low_order_fee=totals.low_order_fee,
    )

    # 8. Open the delivery record — including for zones no courier API touches,
    #    so "what did fulfilment cost" is answerable for the whole country and
    #    not just the automated part of it.
    if data.delivery_method == DeliveryMethodEnum.DELIVERY:
        await lalamove_service.record_order_delivery(
            db, order, zone=totals.zone, cart=cart
        )

    # 9. Put it on a register — check number, business day, pending state. The
    #    branch itself was stamped on the row at insert; this is the POS wiring
    #    around it, and it is best-effort: a business day that will not open is
    #    not a reason to lose a sale that is already paid for.
    await _attach_to_branch(db, order, branch)

    # 10. Tell the kitchen. Best-effort and last, because a push that fails must
    #    not fail an order that is already written and already visible to
    #    anyone who pulls the pending list.
    if order.branch_id is not None:
        try:
            await push_service.notify_order_placed(db, order)
        except Exception:  # pragma: no cover — defensive
            logger.exception(
                "Could not notify %s about %s", order.branch_id, order.order_number
            )

    # 11. Cash orders confirm themselves. A card order is confirmed by Stripe's
    #    `payment_intent.succeeded` and a failure lands it in `payment_failed`,
    #    but cash has no such event — so without this it would sit in `created`
    #    until an admin noticed, which for an order already printing in a
    #    kitchen is a status that means nothing.
    confirmed_as_cash = (
        _is_cash_on_delivery(data) and order.status == OrderStatusEnum.CREATED
    )
    if confirmed_as_cash:
        order.status = OrderStatusEnum.CONFIRMED

    stmt = select(Order).options(*_order_load_options()).where(Order.id == order.id)
    result = await db.execute(stmt)
    response = await to_response(db, result.scalar_one())

    # 12. And tell the customer, here rather than later. The confirmation used
    #    to be sent only by `payment_service.create_session`, one HTTP call
    #    further on — so a browser that closed, timed out or lost signal in
    #    between left a confirmed order printing in the kitchen while the
    #    customer had been told nothing at all. Cash is the only method that
    #    confirms at creation; a card order is still announced by the Stripe
    #    webhook.
    if confirmed_as_cash:
        await _send_confirmation_emails(response)

    return response


async def _send_confirmation_emails(order: OrderResponse) -> None:
    """
    Best-effort: a confirmed order is not un-confirmed by a mail provider
    having a bad minute, and the failure is logged rather than raised.
    """
    try:
        await email_service.send_order_confirmation(order)
        await email_service.send_owner_order_notification(order)
    except Exception:
        logger.exception(
            "Could not send confirmation emails for %s", order.order_number
        )


def _list_row_load_options():
    """
    Turn off the eager loads a list row does not use.

    `Order.payments`, `order_charges`, `order_discounts` and `order_taxes` are
    all `lazy="selectin"` on the model, which is right for reading one order and
    wasteful for listing them: each adds a query per page, and `OrderListResponse`
    reads none of them. On a 2000-row page — which the console offers — that was
    four extra round trips fetching rows nobody looked at.

    `noload` rather than `raiseload`: these rows are handed to a response model
    that never touches the collections, and a future field that does should get
    an empty list rather than an exception in production.
    """
    return [
        noload(Order.payments),
        noload(Order.order_charges),
        noload(Order.order_discounts),
        noload(Order.order_taxes),
    ]


def _item_count_subquery():
    """
    How many units are on an order, counted by the database.

    The list rows need this one number off the items and nothing else about
    them. Both list endpoints used to `selectinload(Order.items)` and sum in
    Python, so a 2000-row page — which the console offers, and which its
    pagination standard requires — hydrated tens of thousands of OrderItem
    objects only to add up a single column and discard them.
    """
    return (
        select(func.coalesce(func.sum(OrderItem.quantity), 0))
        .where(OrderItem.order_id == Order.id)
        .correlate(Order)
        .scalar_subquery()
    )


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
        base_stmt.add_columns(_item_count_subquery().label("item_count"))
        .options(*_list_row_load_options())
        .order_by(Order.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    result = await db.execute(stmt)

    items = []
    for order, count in result.all():
        resp = OrderListResponse.model_validate(order)
        resp.item_count = int(count or 0)
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

    return await to_response(db, order)


async def _email_has_account(db: AsyncSession, email: str | None) -> bool:
    """
    Whether this address can already be signed in to.

    Guests are excluded deliberately. Checkout mints a `…@guest.local` user for
    every anonymous order, so "a user row exists" is true for practically every
    order ever placed and would tell the confirmation page to ask people to sign
    in to an account that has no password.
    """
    if not email:
        return False
    result = await db.execute(
        select(User.id).where(
            func.lower(User.email) == email.lower().strip(),
            User.is_guest.is_(False),
        )
    )
    return result.first() is not None


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
        # And closes on the register, if it ever reached one. Without this the
        # check stays open on the iPad forever: the cashier sees a live order
        # for a cancelled sale, can still add items to it, and it still counts
        # towards the day. Production already has cashier orders sitting
        # `cancelled` with `pos_status = active` from exactly this.
        #
        # Void rather than closed — closed means paid and finished, and this was
        # neither.
        if order.pos_status in _OPEN_ON_THE_REGISTER:
            order.pos_status = PosOrderStatusEnum.VOID.value

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
    return await to_response(db, result.scalar_one())


async def get_all_admin(
    db: AsyncSession,
    status: OrderStatusEnum | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 20,
    channel: str | None = None,
    branch_id: uuid.UUID | None = None,
) -> tuple[list[OrderListResponse], int]:
    """
    Every order, from either channel, newest first.

    `channel` narrows to one. Both are a plain equality now: `061` backfilled
    the storefront orders that predated the column and made it `NOT NULL`, so
    there is no third state to write around.
    """
    base_stmt = select(Order)

    if channel == "counter":
        base_stmt = base_stmt.where(Order.source == OrderSourceEnum.CASHIER.value)
    elif channel == "online":
        base_stmt = base_stmt.where(Order.source == OrderSourceEnum.ONLINE.value)
    if branch_id is not None:
        base_stmt = base_stmt.where(Order.branch_id == branch_id)

    if status:
        base_stmt = base_stmt.where(Order.status == status)
    if search:
        escaped = _escape_like(search)
        base_stmt = base_stmt.where(
            Order.order_number.ilike(f"%{escaped}%", escape="\\")
            | Order.email.ilike(f"%{escaped}%", escape="\\")
            | Order.customer_name.ilike(f"%{escaped}%", escape="\\")
            | Order.customer_phone.ilike(f"%{escaped}%", escape="\\")
        )

    count_result = await db.execute(
        select(func.count()).select_from(base_stmt.subquery())
    )
    total = count_result.scalar() or 0

    stmt = (
        base_stmt.add_columns(_item_count_subquery().label("item_count"))
        .options(*_list_row_load_options())
        .order_by(Order.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    result = await db.execute(stmt)

    items = []
    for order, count in result.all():
        resp = OrderListResponse.model_validate(order)
        resp.item_count = int(count or 0)
        items.append(resp)
    return items, total
