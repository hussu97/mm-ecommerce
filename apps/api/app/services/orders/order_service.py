from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import (
    Integer,
    case,
    cast,
    exists,
    func,
    select,
)
from sqlalchemy import (
    inspect as sa_inspect,
)
from sqlalchemy import (
    update as sql_update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload, selectinload

from app.core import search as search_text
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.core.phone import normalise_phone
from app.models.branch import Branch
from app.models.cart import Cart, CartItem
from app.models.delivery_batch import DELIVERY_TIMEZONE
from app.models.order import DeliveryMethodEnum, Order, OrderItem, OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.models.order_status_event import StatusSourceEnum, acting_as
from app.models.pos_order import OrderSourceEnum, OrderTax
from app.models.product import Product
from app.models.promo_code import PromoCode
from app.models.user import User
from app.schemas.delivery import DeliveryQuoteResponse
from app.schemas.fulfilment import FulfilmentResponse
from app.schemas.order import (
    OrderCreate,
    OrderListResponse,
    OrderResponse,
    OrderStatusStamp,
)
from app.schemas.order_preview import (
    OrderPreviewPromo,
    OrderPreviewRequest,
    OrderPreviewResponse,
    UnavailableItem,
)
from app.services import cart_service, email_service, promo_code_service, push_service
from app.services.catalog import availability_service
from app.services.catalog.storefront_visibility import is_website_product_visible
from app.services.couriers import courier_catalog, lalamove_service
from app.services.delivery import delivery_promise, delivery_service, fulfilment_service
from app.services.delivery.delivery_zone_service import Zone
from app.services.orders import (
    order_economics,
    order_fees,
    order_lifecycle,
    order_pricing,
)

# Re-exported for the callers and tests that have always found it here. The
# map itself, and the machinery that enforces it, live in `order_lifecycle` —
# the one module allowed to assign `Order.status`.
from app.services.orders.order_lifecycle import VALID_TRANSITIONS

# Same reason, for the money. Every figure an order is written with now comes
# out of `order_pricing`, which is also what `POST /orders/preview` calls — the
# two cannot disagree because there is only one implementation. These names are
# kept here because callers and tests have always found them at this address.
from app.services.orders.order_pricing import (  # noqa: F401
    VAT_RATE,
    OrderTotals,
    TaxBreakdown,
    low_order_fee_for,
    tax_breakdown,
)
from app.services.payments import payment_methods
from app.services.pos import pos_order_service

logger = logging.getLogger(__name__)

__all__ = [
    "SUPPORTED_LOCALES",
    "VALID_TRANSITIONS",
    "normalise_locale",
    "create_order",
    "preview_order",
    "to_response",
    "get_all_admin",
    "get_by_order_number",
    "get_for_notification",
    "get_user_orders",
    "publish_to_register",
    "stamp_packed",
    "update_status",
]


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
    reached = await fulfilment_service.reached_at(db, order)
    response = OrderResponse.model_validate(order)
    response.email_has_account = await _email_has_account(db, order.email)
    response.fulfilment = FulfilmentResponse.of(
        await fulfilment_service.for_order(db, order, reached=reached)
    )
    response.status_history = [
        OrderStatusStamp(status=status, at=at)
        for status, at in sorted(reached.items(), key=lambda pair: pair[1])
    ]
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


def _priceable_cart_options():
    """
    Everything pricing a basket touches, loaded up front.

    `_compute_item_totals` reads each line's product, and
    `is_website_product_visible` reads that product's category. A lazy load
    inside async SQLAlchemy is not a slow query, it is a `MissingGreenlet` — so
    this is a correctness requirement rather than a performance one, and it is
    written once because both the order and its preview need exactly it.

    **And both of them also ask what the branch has run out of.** That walk —
    `product_modifiers → modifier → options`, in `availability_service.
    blocking_groups` — was missing here, so `preview_order` and step 5a of
    `create_order` both lazy-loaded it and 500ed. Only at a branch with
    something actually out of stock, because `unavailable_cart_lines` returns
    before the per-line loop otherwise: dormant through every test and every
    ordinary day, and "checkout is broken" on the days the shop had run out of
    something. The chain is owned by the service that needs it rather than
    copied to here, so changing `blocking_groups` cannot leave this behind
    again.
    """
    return [
        selectinload(Cart.items)
        .joinedload(CartItem.product)
        .joinedload(Product.category),
        *availability_service.cart_load_options(),
    ]


async def find_priceable_cart(
    db: AsyncSession,
    user_id: uuid.UUID | None,
    session_id: str | None,
) -> Cart | None:
    """
    The basket this request belongs to, loaded ready to price, or nothing.

    Priority: user_id cart → session_id cart (fallback).

    The fallback matters for the guest-checkout flow: the frontend creates a
    guest JWT just before submitting the order, so ``user_id`` is set in the
    token but the cart was built using ``session_id``.  Without the fallback
    the lookup would return nothing and raise "Cart is empty".

    Answers `None` rather than refusing, because "there is nothing in the
    basket yet" is an ordinary state for `preview_order` and a fatal one for
    `create_order`; see `_locate_cart` for the second.
    """
    # Through `cart_service.cart_for_identity` rather than `scalar_one_or_none()`,
    # which is what used to be here and asserts a uniqueness `carts` lacked.
    # Two concurrent add-to-cart calls for one guest made two rows, and from
    # then on this raised `MultipleResultsFound` — so the checkout 500ed for the
    # same reason the basket did. Migration 114 stops the duplicate being
    # created; this stops one that already exists from refusing a sale.
    cart: Cart | None = None
    options = tuple(_priceable_cart_options())

    if user_id:
        cart = await cart_service.cart_for_identity(db, user_id, None, options=options)

    # Fallback to session-id cart (guest user who just received a JWT)
    if (not cart or not cart.items) and session_id:
        session_cart = await cart_service.cart_for_identity(
            db, None, session_id, options=options
        )
        if session_cart and session_cart.items:
            cart = session_cart

    return cart


async def _locate_cart(
    db: AsyncSession,
    user_id: uuid.UUID | None,
    session_id: str | None,
) -> Cart:
    """The basket, or a refusal. An order cannot be written without one."""
    if not user_id and not session_id:
        raise BadRequestError("session_id is required for guest checkout")

    cart = await find_priceable_cart(db, user_id, session_id)
    if not cart or not cart.items:
        raise BadRequestError("Cart is empty")

    return cart


def _compute_item_totals(
    cart: Cart, *, strict: bool = True
) -> tuple[list[dict], Decimal]:
    """
    Validate cart items and compute the order subtotal.

    Returns (items_data, subtotal).
    items_data is the list of dicts used to create OrderItem rows.

    `strict=False` prices the same basket without the two refusals below, for
    `preview_order`. A checkout still being filled in has not necessarily typed
    the gift message yet, and refusing to name a total until it has would leave
    the whole summary blank on a screen whose job is to show one. Nothing is
    written from a preview, and `create_order` still refuses both — the customer
    is stopped at the button rather than at the total.
    """
    subtotal = Decimal("0.00")
    items_data: list[dict] = []

    for cart_item in cart.items:
        product = cart_item.product
        if not is_website_product_visible(product):
            if strict:
                raise BadRequestError("A product in your cart is no longer available")
            # A preview leaves it out rather than refusing: something that
            # cannot be bought is not part of what this order would cost. It
            # also keeps the loop safe on a line whose product row has gone.
            continue

        selected_options = cart_item.selected_options or []

        # A product that asks for a message must have one by the time it is
        # bought. The cart tolerates a blank — the field appears only after the
        # item is in the basket, so empty there means "not finished yet" — but
        # an order is the last moment anyone can be asked, and a gift note
        # delivered blank is a paid item that does nothing.
        note = cart_service.clean_note(cart_item.personalisation_note)
        if strict and product.personalisation_type and not note:
            raise BadRequestError(
                f"Add your message for '{product.name}' before checking out"
            )

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
                "personalisation_note": note,
                # Carried out of here so the tax breakdown can be built from the
                # same lines the order is built from, rather than re-reading
                # every product a second time. Popped before the row is written
                # — `order_items` has no such column, and the group is a
                # property of the product, not of a line that happened to sell
                # it on a Tuesday.
                "_tax_group_id": getattr(product, "tax_group_id", None),
            }
        )

    return items_data, subtotal


def _tax_lines_of(items_data: list[dict]) -> list[tuple[uuid.UUID | None, Decimal]]:
    """
    The basket as the pricing engine wants it: `(tax_group_id, gross)` a line.

    `_tax_group_id` rode out of `_compute_item_totals` on the item dict so the
    tax breakdown could be built from the same lines the order is built from,
    rather than re-reading every product a second time. This is where it is
    read; `_persist_order` pops it, because `order_items` has no such column.
    """
    return [
        (item.get("_tax_group_id"), Decimal(str(item["total_price"])))
        for item in items_data
    ]


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


def _normalised_payment_method(data: OrderCreate) -> str:
    """
    What to stamp on the order: `card` or `cod`, never a gateway's name.

    The checkout used to send `stripe` and this column used to store it, which
    made "how did they pay" and "who processed it" the same field — and they
    stop being the same field the moment there is a second processor. One
    helper, shared with `payment_service`, so the two cannot disagree about
    what an incoming value means.
    """
    return payment_methods.normalise_method(data.payment_method)


def _is_cash_on_delivery(data: OrderCreate) -> bool:
    return _normalised_payment_method(data) == payment_methods.COD


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


async def stamp_packed(db: AsyncSession, order: Order, *, note: str) -> bool:
    """
    Say the box is finished, without anybody pressing a button to say it.

    Nobody presses it any more. The register's Accept is what calls a driver
    now, and a second press half an hour later to state a fact the system can
    work out was the last piece of the flow that existed only because the
    software could not tell. So the two things that *do* know say it instead:
    a courier booking that succeeded, and a kitchen finishing its last ticket.

    `packed` itself stays. It is what the customer's tracker draws, what the
    "on its way" email hangs off, and what `out_for_delivery` transitions from.
    Only the hand that used to set it is gone.

    Returns whether it moved anything. False is the ordinary case on a second
    call — an order already packed, already out for delivery, or cancelled
    between the booking and this line — and is not a failure.

    Deliberately routed through `update_status` rather than assigning the
    column. That function owns the transition rules, the status event and the
    mail; a second implementation here is how the register and the console would
    come to disagree about what packing an order does. It calls
    `assign_or_dispatch` on the way through, which returns untouched because
    whatever called this has just booked the driver.

    **The shop is told first, if it has not been told yet.** `confirmed →
    packed` is a legal transition, so an order whose van was booked before its
    arrival fell due went straight past `arrived_at_pos` — and
    `publish_to_register` hangs off that status and nothing else. The order was
    never attached to a register, never printed, and never rang the alarm: no
    check number, no ticket, nothing to accept. It could not even be recovered
    by the arrival sweep afterwards, because that sweep looks for orders still
    at `confirmed` and this one was already `packed`.

    Both bookings reach here, so both were affected: an admin pressing Dispatch
    now on a single order, and a run going out — including on schedule, where
    the batch is booked in the same sweep tick that would otherwise have landed
    its orders a moment later. A driver was called for a box nobody in the
    kitchen had been asked to make.

    So a booking that finds the order still at `confirmed` lands it first, which
    is the same act the sweep would have performed and in the same order: the
    register hears about it with the courier reference already on the ticket,
    and then the box is stamped finished. `land` is idempotent about the
    dispatch — `assign_or_dispatch` returns untouched on an order that already
    has a courier order — so telling the shop cannot book a second van.
    """
    if order.status == OrderStatusEnum.CONFIRMED:
        # Imported here for the same reason as `order_service` below: this
        # module sits under the arrival machinery, and closing the cycle at
        # import time is a crash on boot.
        from app.services.delivery import arrival_service

        # `note` is why the booking happened, which is exactly why the shop is
        # being told now — "lalamove booking accepted", "batch 1 booked".
        await arrival_service.land(db, order, because=note)

    if OrderStatusEnum.PACKED not in VALID_TRANSITIONS.get(order.status, set()):
        return False
    with acting_as(StatusSourceEnum.SYSTEM.value, note=note):
        await update_status(db, order.order_number, OrderStatusEnum.PACKED)
    return True


async def publish_to_register(
    db: AsyncSession, order: Order, branch: Branch | None = None
) -> None:
    """
    Tell the shop about a website order: check number, business day, state, push.

    **Called when the money lands, not when the row is written.** It used to run
    inside `create_order`, one line after the insert — so every checkout that
    reached our database reached a counter, whether or not it was ever paid for.
    A card order sits at `created` until its gateway says the money moved, and
    an abandoned checkout stays there forever; both arrived on the iPad as
    `pos_status = pending`, sounded the unaccepted-order alarm, and left a
    cashier baking against a payment that had not happened and might never.
    Cash and zero-total orders confirm at creation and so still publish there —
    the trigger is the confirmation, not the endpoint.

    Deliberately best-effort, and unchanged in that. Which kitchen is making it
    is part of the order and is written with it; whether the till could open a
    business day for it is not worth losing a paid sale over. What this misses
    is findable by `is_pos = false AND source = 'online'`.

    Idempotent. Every confirmation path funnels through here and some of them
    overlap — a cash order confirmed by `create_order` and then asked for a
    payment session again. `attach_online_order` already guards on the check
    number; the push is guarded on the same fact, because the one thing that
    must not repeat is the alarm.
    """
    if order.source != OrderSourceEnum.ONLINE.value:
        return
    if branch is None:
        if order.branch_id is None:  # pragma: no cover — column is NOT NULL
            return
        branch = await db.get(Branch, order.branch_id)
        if branch is None:  # pragma: no cover — FK is RESTRICT
            return

    first_time = order.check_number is None

    try:
        await pos_order_service.attach_online_order(db, order, branch)
    except Exception:  # pragma: no cover — defensive
        logger.exception(
            "Order %s could not be put on %s's register",
            order.order_number,
            branch.reference,
        )

    # Best-effort and last, because a push that fails must not fail an order
    # that is already written and already visible to anyone who pulls the
    # pending list.
    if first_time and order.branch_id is not None:
        try:
            await push_service.notify_order_placed(db, order)
        except Exception:  # pragma: no cover — defensive
            logger.exception(
                "Could not notify %s about %s", order.branch_id, order.order_number
            )


async def _persist_order(
    db: AsyncSession,
    data: OrderCreate,
    user_id: uuid.UUID | None,
    cart: Cart,
    items_data: list[dict],
    totals: order_pricing.OrderTotals,
    promo_code_used: str | None,
    promo_obj: PromoCode | None,
    fallback_email: str | None = None,
    branch: Branch | None = None,
    promised: delivery_promise.DeliveryPromise | None = None,
) -> Order:
    """
    Write the order, its items, and update promo usage atomically.
    Clears the cart on success and returns the persisted order.

    Every money column comes off `totals`, which arrived from
    `order_pricing.compute_order_totals` and is the same object
    `POST /orders/preview` answered from. This used to take eight loose
    `Decimal`s in a thirteen-argument list and then work the VAT out for itself
    from the item lines — so the order was quoted from one calculation and
    written from another, and they agreed only because today's catalogue has a
    single tax rate in it.
    """
    # `orders.email` is not nullable and is what every downstream lookup keys
    # on. `OrderCreate.email` is now required, so on the storefront path
    # `data.email` always wins and the fallback never fires.
    #
    # The fallback stays anyway, and not out of nostalgia. It is the signed-in
    # caller's own account address, so it is a *better* value than a failure for
    # anything that reaches here without a checkout body behind it, and the
    # column below cannot take a null. The identically-named argument on
    # `preview_order` is unambiguously still load-bearing — `OrderPreviewRequest`
    # keeps email optional by design, because a form being filled in has to be
    # priced before it is finished — and removing this one would leave the two
    # sides of the "preview and creation must agree" pair reading from different
    # identities. Two tokens is the whole cost.
    #
    # What it no longer does is stand in for a customer who declined to give an
    # address. That case used to land here as the generated `…@guest.local` the
    # auth layer mints — an address the mailer knows not to write to, so those
    # orders were confirmed to nobody, which is the second reason email became
    # required. The schema refuses them now, before this line is reached.
    order_email = data.email or fallback_email
    if not order_email:
        raise BadRequestError("An email address or an active session is required")

    address_snapshot: dict | None = (
        data.shipping_address.model_dump(mode="json") if data.shipping_address else None
    )

    # The number the customer gave, written here rather than left to the
    # register attach below. That attach is deliberately best-effort — a till
    # that cannot open a business day must not lose a paid sale — so a phone
    # written only there is a phone that is sometimes missing. It is an identity
    # for the new-customer coupon now, and a coupon rule cannot be enforced off a
    # column that is populated most of the time.
    #
    # Stored canonical where it can be read as a number at all, because this
    # column is now matched against on every coupon validation and two spellings
    # of one handset must not read as two customers. Falls back to whatever was
    # given when it cannot be parsed — a number nobody can normalise is still a
    # number the driver has to ring.
    contact_phone = None
    if data.shipping_address is not None:
        given = (data.shipping_address.phone or "").strip()
        contact_phone = normalise_phone(given) or given or None

    order = Order(
        order_number=await _generate_order_number(db),
        user_id=user_id,
        email=order_email,
        customer_phone=contact_phone,
        locale=normalise_locale(data.locale),
        delivery_method=data.delivery_method,
        delivery_fee=totals.delivery_fee,
        low_order_fee=totals.low_order_fee,
        subtotal=totals.subtotal,
        discount_amount=totals.discount_amount,
        total=totals.total,
        # The VAT, from each product's own tax group rather than a module
        # constant. Identical arithmetic for today's catalogue — every active
        # product is in one 5% inclusive group — and the point is what happens
        # when one of them is not: a zero-rated line stops being charged VAT, a
        # rate change becomes an edit, and `order_taxes` finally gets rows for a
        # storefront order, which is why a website receipt printed no VAT
        # breakdown while a counter receipt printed one from the same table.
        vat_rate=totals.vat_rate,
        vat_amount=totals.vat_amount,
        total_excl_vat=totals.total_excl_vat,
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
        # `card` or `cod`, never the name of a processor. Normalised on the way
        # in so a browser still posting `stripe` cannot write a gateway into the
        # column that answers "how did the customer choose to pay".
        payment_method=_normalised_payment_method(data),
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
        # `_tax_group_id` rode along so the breakdown could be built from the
        # same lines; `order_items` has no such column.
        db.add(
            OrderItem(
                order_id=order.id,
                **{k: v for k, v in item.items() if not k.startswith("_")},
            )
        )

    # The invoice's tax rows, one per distinct rate. Written here rather than
    # derived on read, because a tax group can be edited tomorrow and an invoice
    # has to keep saying what was actually charged today.
    for line in totals.taxes:
        db.add(
            OrderTax(
                order_id=order.id,
                tax_id=uuid.UUID(line.tax_id) if line.tax_id else None,
                name=line.name,
                rate=line.rate,
                taxable_amount=line.taxable_amount,
                amount=line.amount,
            )
        )

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
                # The same sentence `validate` uses for a spent campaign — this
                # is that refusal, arriving a few milliseconds later because
                # somebody else took the last one between validation and write.
                # A customer who loses the race should not get different words
                # from one who was simply too late.
                raise BadRequestError("This code has been fully claimed")
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

    # What taking this money will cost, written onto the order now that the
    # total is final. Stored rather than recomputed on every admin page view,
    # so the shop can sum a month of it — see `order_fees`.
    await order_fees.stamp(db, order)
    return order


async def _validate_promo(
    db: AsyncSession,
    *,
    code: str,
    subtotal: Decimal,
    user_id: uuid.UUID | None,
    email: str | None,
    shipping_address,
    delivery_method: DeliveryMethodEnum,
    enforce_phone_verification: bool,
):
    """
    Judge a coupon against the identity this order would be written under.

    One assembly of "who is asking", shared by the preview and the order, because
    a new-customer coupon is refused on an account, an email *or* a phone that
    has ordered before — and a preview that sent a different set from the one
    `create_order` sends is a preview that shows a discount the pay button then
    refuses. The only thing the two callers vary is the last argument: the
    preview reports the phone gate, the order enforces it.
    """
    return await promo_code_service.validate(
        db,
        code,
        subtotal,
        user_id=user_id,
        email=email,
        phone=(
            (shipping_address.phone or "").strip() or None
            if shipping_address is not None and getattr(shipping_address, "phone", None)
            else None
        ),
        # The phone gate is a delivery rule — see `validate`. Passing the
        # method means a collection is not judged on a number it was never
        # asked for: before this, a pickup order carrying a gated code was
        # refused outright, because the only phone this call knows about
        # lives on a shipping address a pickup does not have.
        delivery_method=delivery_method,
        enforce_phone_verification=enforce_phone_verification,
    )


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
        # The same identity the order is about to be written under, so the
        # server-side check cannot disagree with the one the checkout showed.
        validation = await _validate_promo(
            db,
            code=data.promo_code,
            subtotal=subtotal,
            user_id=user_id,
            email=data.email or fallback_email,
            shipping_address=data.shipping_address,
            delivery_method=data.delivery_method,
            # The one caller that enforces rather than reports. Everything up to
            # here has been provisional; this is the row being written.
            enforce_phone_verification=True,
        )
        if not validation.valid:
            # Told apart from every other refusal because it is the only one the
            # customer can still fix from the checkout — the address form has
            # the button, and the wording names it.
            if validation.requires_phone_verification and not validation.phone_verified:
                raise BadRequestError(
                    "Verify your mobile number to use this code on a delivery order"
                )
            # Verbatim, with no prefix. Every one of these used to be shipped as
            # `Promo code: {message}`, which produced "Promo code: Promo code is
            # not active" in the toast and, worse, gave nine different refusals
            # one indistinguishable opening — the customer read the prefix,
            # concluded they had mistyped, and retyped a code that was expired,
            # spent, or not for them. `validate` now writes each refusal as the
            # sentence the customer should read, and each one names the code
            # itself, so there is nothing left for a prefix to add.
            raise BadRequestError(validation.message)
        discount_amount = validation.discount_amount
        promo_obj = await promo_code_service.get_promo(db, data.promo_code)
        # The English spelling, whichever one was typed. Two codes naming one
        # coupon must leave one value on the order, or every count that reads
        # this column has to know about both.
        promo_code_used = promo_obj.code

    # 4. Price it: delivery fee, small-basket fee, VAT, total.
    #    The same call `POST /orders/preview` made while the customer was still
    #    reading the summary — see `order_pricing`. Nothing here re-derives a
    #    figure the preview showed, which is the only way the two can be
    #    guaranteed to agree.
    address = data.shipping_address
    totals = await order_pricing.compute_order_totals(
        db,
        lines=_tax_lines_of(items_data),
        delivery_method=data.delivery_method,
        discount_amount=discount_amount,
        # The same address `_persist_order` will stamp on the order, so the fee
        # and the pin it was priced for cannot disagree.
        latitude=address.latitude if address else None,
        longitude=address.longitude if address else None,
        address=address.address_line_1 if address else None,
        # The same identity the preview priced against, so a pilot account's
        # free delivery survives from the summary onto the order.
        user_id=user_id,
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

    # 5a. Can that kitchen actually make this basket?
    #
    #     Only answerable here: the catalogue hid nothing, because a product is
    #     hidden from browsing only when *every* branch is out of it, and the
    #     branch this order goes to was not known until the line above. The
    #     shopper may also have changed the address at the checkout and moved
    #     the order to a different kitchen after adding everything.
    #
    #     Refused with a code rather than a sentence so the web app can open the
    #     resolution screen instead of showing a toast the customer cannot act
    #     on. Checked again here even though that screen exists, because a
    #     screen is a courtesy and this is the guarantee.
    unavailable = await availability_service.unavailable_cart_lines(
        db, cart=cart, branch_id=branch.id
    )
    if unavailable:
        raise ConflictError(
            "Some items aren't available at the branch serving this address",
            code=availability_service.UNAVAILABLE_AT_BRANCH,
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
    promised: delivery_promise.DeliveryPromise | None = None
    if data.delivery_method == DeliveryMethodEnum.DELIVERY:
        promised = await delivery_promise.promise_for_zone(db, totals.zone)

    # 6. Claim stock for stock-tracked products (fails if any is out of stock)
    await _decrement_stock(db, cart)

    # 7. Persist order rows and clear cart.
    #    The constructor sets `status`, which is a transition like any other and
    #    is recorded as one — the first row of every order's history is written
    #    from inside this call.
    with acting_as(StatusSourceEnum.CHECKOUT.value, actor_id=user_id):
        order = await _persist_order(
            db,
            data,
            user_id,
            cart,
            items_data,
            # One object, not eight loose `Decimal`s. The argument list used to
            # be thirteen long with four adjacent money figures in it, and a
            # tuple whose elements can be swapped without a type error is how a
            # small-basket fee gets charged to the customer as delivery.
            totals,
            promo_code_used,
            promo_obj,
            fallback_email,
            branch,
            promised,
        )

    # 8. Open the delivery record — including for zones no courier API touches,
    #    so "what did fulfilment cost" is answerable for the whole country and
    #    not just the automated part of it.
    if data.delivery_method == DeliveryMethodEnum.DELIVERY:
        await lalamove_service.record_order_delivery(
            db, order, zone=totals.zone, cart=cart
        )

    # 9. Cash orders confirm themselves. A card order is confirmed by its
    #    gateway's success event and a failure lands it in `payment_failed`,
    #    but cash has no such event — so without this it would sit in `created`
    #    until an admin noticed, which for an order the customer is walking in
    #    to collect is a status that means nothing.
    confirmed_as_cash = (
        _is_cash_on_delivery(data) and order.status == OrderStatusEnum.CREATED
    )
    if confirmed_as_cash:
        # 10. Confirming is also what puts it on a register and tells the
        #    kitchen — `transition` publishes on the way through, so a counter
        #    hears about a website order when it has been paid for. A card
        #    order stays `created` here and is confirmed (and published) by the
        #    payment webhook instead; see `publish_to_register` for what used
        #    to happen here and why it moved.
        with acting_as(StatusSourceEnum.CHECKOUT.value, note="cash on collection"):
            await order_lifecycle.transition(db, order, OrderStatusEnum.CONFIRMED)

    stmt = select(Order).options(*_order_load_options()).where(Order.id == order.id)
    result = await db.execute(stmt)
    response = await to_response(db, result.scalar_one())

    # 11. And tell the customer, here rather than later. The confirmation used
    #    to be sent only by `payment_service.create_session`, one HTTP call
    #    further on — so a browser that closed, timed out or lost signal in
    #    between left a confirmed order printing in the kitchen while the
    #    customer had been told nothing at all. Cash is the only method that
    #    confirms at creation; a card order is still announced by the Stripe
    #    webhook.
    if confirmed_as_cash:
        await _send_confirmation_emails(response)

    return response


async def preview_order(
    db: AsyncSession,
    data: OrderPreviewRequest,
    user_id: uuid.UUID | None,
    fallback_email: str | None = None,
    session_id: str | None = None,
) -> OrderPreviewResponse:
    """
    What this basket would cost if the customer pressed the button now.

    Deliberately written directly above `create_order` and in the same order of
    operations — basket, coupon, price, answer — because the property that
    matters is that the two agree, and the cheapest way to keep two functions
    agreeing is to make a divergence visible in the diff. Step for step:

    * the same basket lookup a guest checkout uses;
    * the same line pricing, minus the two refusals an unfinished form should
      not be judged by (see `_compute_item_totals`);
    * the same coupon call against the same assembled identity, reporting the
      phone gate where `create_order` enforces it;
    * the same `order_pricing.compute_order_totals`, which is the whole point.

    Read-only. It writes nothing about the order — no row, no reservation, no
    promo use. The one thing it does persist is the courier's estimate against
    the cart, which is not about this order at all: it is how
    `record_order_delivery` later learns what the run was quoted at, and it is
    what the `/delivery/quote` call this endpoint replaces on the checkout was
    already doing. Losing it would blank the freight-margin report.

    Never raises for a state the customer can still fix. An address nothing can
    be delivered to, a coupon that no longer applies, a basket that is still
    loading: all of them are answers, because a checkout with no total on it is
    worse than a checkout showing a total with a warning next to it. The refusals
    live on the pay button, where they can be acted on.
    """
    # The same lookup and the same eager loading `create_order` does — not
    # `cart_service.find_cart`, which selects the row on its own and would leave
    # every product behind a lazy load this cannot perform.
    cart = await find_priceable_cart(db, user_id, data.session_id or session_id)

    # The second thing this endpoint persists, and for the same reason as the
    # first: it is the only moment the information exists. A guest who types
    # their address into the checkout and then closes the tab leaves a basket
    # nobody can write to, which `tasks/conversion-audit.md` names as the single
    # blocker under abandoned-cart recovery. Guest baskets only, and the address
    # is recorded rather than judged — see `cart_service.remember_checkout_email`.
    await cart_service.remember_checkout_email(db, cart, data.email or fallback_email)

    items_data: list[dict] = []
    if cart is not None and cart.items:
        items_data, _ = _compute_item_totals(cart, strict=False)

    lines = _tax_lines_of(items_data)
    subtotal = sum((gross for _, gross in lines), Decimal("0.00"))

    promo: OrderPreviewPromo | None = None
    discount_amount = Decimal("0.00")
    if data.promo_code:
        validation = await _validate_promo(
            db,
            code=data.promo_code,
            subtotal=subtotal,
            user_id=user_id,
            email=data.email or fallback_email,
            # The checkout's own phone field, wrapped in the shape the shared
            # identity assembly reads. A delivery's number lives on its shipping
            # address at order time and in the form before that; the coupon rule
            # cares only about the digits.
            shipping_address=SimpleNamespace(phone=data.phone),
            delivery_method=data.delivery_method,
            # Reports rather than enforces — see `promo_code_service.validate`.
            # The customer is still on the form that has the verify button.
            enforce_phone_verification=False,
        )
        if validation.valid:
            discount_amount = validation.discount_amount
        promo = OrderPreviewPromo(
            code=data.promo_code.strip().upper(),
            valid=validation.valid,
            message=validation.message,
            discount_amount=float(discount_amount),
            requires_phone_verification=validation.requires_phone_verification,
            phone_verified=validation.phone_verified,
        )

    # One pricing call for the fee, the free-delivery countdown, the arrival
    # estimate *and* the totals below. Priced against the discounted basket
    # because free delivery is judged on what the customer actually pays — the
    # same figure `compute_order_totals` prices against, so the threshold the
    # countdown quotes is the threshold the fee was decided by.
    #
    # Asked even for a collection, and deliberately: the checkout renders both
    # options side by side, and the delivery one has to be able to say what it
    # would cost while pickup is the one selected. This is exactly what the
    # `/delivery/quote` call it replaces did — that endpoint never knew the
    # method either — so it is the same number of courier calls, not one more.
    # With no pin there is no courier call at all; `price()` returns before it.
    quote_payload, priced = await delivery_service.quote_priced(
        db,
        subtotal - discount_amount,
        latitude=data.latitude,
        longitude=data.longitude,
        cart=cart,
        address=data.address,
        user_id=user_id,
        email=data.email or fallback_email,
    )

    totals = await order_pricing.compute_order_totals(
        db,
        lines=lines,
        delivery_method=data.delivery_method,
        discount_amount=discount_amount,
        priced=priced,
        # The same identity `quote_priced` above was given, or the summary and
        # the totals under it would disagree about a pilot account's fee.
        user_id=user_id,
        email=data.email or fallback_email,
        # A preview answers; it does not refuse. See `compute_order_totals`.
        strict=False,
    )

    # The kitchen this pin resolves to, and what it has run out of.
    #
    # `resolve_branch` is the same call `create_order` makes at step 5, from the
    # same zone this pricing produced — so the branch the customer is warned
    # about is the branch the order would actually go to. A preview reports;
    # `create_order` is what refuses.
    branch = await resolve_branch(
        db,
        totals.zone,
        pickup_branch_id=(
            data.pickup_branch_id
            if data.delivery_method == DeliveryMethodEnum.PICKUP
            else None
        ),
    )
    unavailable = await availability_service.unavailable_cart_lines(
        db, cart=cart, branch_id=branch.id if branch else None
    )

    return OrderPreviewResponse(
        subtotal=float(totals.subtotal),
        discount_amount=float(totals.discount_amount),
        delivery_fee=(
            float(totals.delivery_fee) if totals.delivery_fee_known else None
        ),
        low_order_fee=float(totals.low_order_fee),
        vat_rate=float(totals.vat_rate),
        vat_amount=float(totals.vat_amount),
        total_excl_vat=float(totals.total_excl_vat),
        total=float(totals.total),
        promo=promo,
        delivery=DeliveryQuoteResponse(**quote_payload),
        unavailable_items=[
            UnavailableItem(
                product_id=str(line.product_id),
                product_name=line.product_name,
                reason=line.reason,
                unavailable_options=list(line.unavailable_option_names),
            )
            for line in unavailable
        ],
    )


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


def _cost_of_sale_subquery():
    """
    What the courier charged for this order, as a scalar the list can subtract.

    `cost_total` is the invoice and `quoted_cost` is the quote; the first is the
    truth and arrives late, so the second stands in until it does — the same
    preference `order_economics` applies one order at a time. Null where there
    is no delivery row at all, which is a counter sale, and null where a
    third-party zone bills nothing per order.
    """
    return (
        select(func.coalesce(OrderDelivery.cost_total, OrderDelivery.quoted_cost))
        .where(OrderDelivery.order_id == Order.id)
        .correlate(Order)
        .limit(1)
        .scalar_subquery()
    )


def _net_value_expression(cost_of_sale):
    """
    What the shop keeps on a row, in SQL.

    Mirrors `OrderEconomics.net` exactly and is tested against it. It lives here
    rather than being borrowed from that module because the two answer the same
    question at different scales — one order on a screen, two thousand rows in a
    page — and a per-row service call would be two thousand round-trips for one
    column.

    **Null propagates deliberately.** An aggregator order whose commission rate
    nobody has configured has an unknowable net, and `NULL` is how that reaches
    the console as a dash. `coalesce`-ing it to zero would show the order
    keeping every dirham, which is the exact misreading this column exists to
    prevent — so the coalesce below is applied only to the parts that are
    genuinely zero when absent (a counter sale's courier, a cash order's
    processor), never to a fee we were simply never told.
    """
    known_cost = case(
        # A marketplace order's cost of sale is its commission; nobody invoices
        # us for a van. A dispatched order's is the courier. Neither has both.
        (
            Order.source == OrderSourceEnum.AGGREGATOR.value,
            Order.aggregator_fee,
        ),
        else_=func.coalesce(cost_of_sale, 0),
    )
    return (
        Order.total
        - known_cost
        - func.coalesce(Order.payment_fee, 0)
        - func.coalesce(Order.refunded_amount, 0)
    )


def _list_row_load_options():
    """
    Pin the four POS collections to empty on list rows.

    They used to be `lazy="selectin"` on the model and this was the escape
    hatch: four extra queries per page, fetching rows `OrderListResponse` never
    reads. The model is lazy by default now, so no query fires either way and
    the performance job here is done.

    Kept as a guardrail rather than deleted. The lazy default answers a
    forgotten access with `MissingGreenlet`, and these rows are handed to a
    response model maintained far from this query — a future field that touches
    a collection should degrade to an empty list on the console's order list,
    not a 500 across every page of it. `noload` states that choice where the
    query is built.
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

    # The direct-cost column, computed in the same pass as the rows. `subtotal`
    # is the goods at menu price *before* discount — the denominator held still
    # on purpose, so a coupon shows up as the cost it is instead of shrinking
    # the base it is measured against.
    net_value = _net_value_expression(_cost_of_sale_subquery())
    cost_cover = net_value / func.nullif(Order.subtotal, 0) * 100

    stmt = (
        base_stmt.add_columns(
            _item_count_subquery().label("item_count"),
            net_value.label("net_value"),
            cost_cover.label("cost_cover"),
        )
        .options(*_list_row_load_options())
        .order_by(Order.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    result = await db.execute(stmt)

    threshold = order_economics.DIRECT_COST_THRESHOLD
    items = []
    for order, count, net, cover in result.all():
        resp = OrderListResponse.model_validate(order)
        resp.item_count = int(count or 0)
        resp.net_value = float(net) if net is not None else None
        resp.cost_cover = float(cover) if cover is not None else None
        # Three-valued, and the null is the point: an unconfigured commission
        # rate makes the answer unknowable, and rendering that as a failure is
        # how a seeding gap becomes a fortnight of chasing the wrong orders.
        resp.covers_direct_cost = (
            None if cover is None else Decimal(str(cover)) >= threshold
        )
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

    # Validation, the write, and everything the write implies — the refund, the
    # restock, the register void, the courier cancellation — all live in
    # `order_lifecycle.transition`, where they fire identically for the admin,
    # a webhook, the checkout and the till. This function is the admin-shaped
    # doorway to it: look up by number, carry the notes, answer with the
    # response model.
    #
    # `extra_from` is what makes this doorway different from the others, and
    # only here: a person may take an order back out of `undelivered` or
    # `cancelled` and mark it delivered, because those are written down when
    # something has definitively failed and a person is sometimes wrong about
    # that — a driver who reports a failed handover and then finds the
    # customer, a cancellation keyed against the wrong order number. It stays
    # out of `VALID_TRANSITIONS` on purpose: that map is also read by the
    # courier webhooks, and a late `delivered` push must not resurrect an order
    # the shop has already written off.
    # `extra_from` widens where a move may start for this one doorway. Two cases
    # stack: the admin recovering a written-off order (delivered ← undelivered/
    # cancelled), and cancelling an aggregator order that is already `packed` —
    # the shop changed its mind after the rider was called, and GrubOps decides
    # whether force-cancel still lands. Neither belongs in `VALID_TRANSITIONS`
    # (the courier webhooks read that too); both are the console's to allow.
    extra_from = set(order_lifecycle.ADMIN_RECOVERABLE.get(new_status, frozenset()))
    if (
        new_status == OrderStatusEnum.CANCELLED
        and order.source == OrderSourceEnum.AGGREGATOR.value
    ):
        extra_from |= order_lifecycle.AGGREGATOR_CANCELLABLE_FROM

    moved = await order_lifecycle.transition(
        db,
        order,
        new_status,
        extra_from=extra_from,
    )
    if not moved:
        # Already there. `transition` treats that as a quiet no-op, which is
        # right for a webhook repeating itself and wrong for a person: an admin
        # asking for a move that cannot happen has always been told so.
        raise BadRequestError(
            f"Cannot transition order from '{order.status}' to '{new_status}'. "
            "The order is already in that status."
        )
    if admin_notes is not None:
        order.admin_notes = admin_notes

    await db.flush()
    await db.refresh(order)

    stmt = select(Order).options(*_order_load_options()).where(Order.id == order.id)
    result = await db.execute(stmt)
    return await to_response(db, result.scalar_one())


#: An aggregator courier code → the prefix its `aggregator_channel` display name
#: starts with, so a `courier=keeta` filter catches "Keeta 2.0" too.
_AGGREGATOR_CHANNEL_PREFIX: dict[str, str] = {
    "talabat": "talabat",
    "keeta": "keeta",
    "noon_food": "noon",
    "deliveroo": "deliveroo",
    "careem": "careem",
}


async def get_all_admin(
    db: AsyncSession,
    status: OrderStatusEnum | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 20,
    channel: str | None = None,
    courier: str | None = None,
    branch_id: uuid.UUID | None = None,
) -> tuple[list[OrderListResponse], int]:
    """
    Every order, from any channel, newest first.

    `channel` narrows to one source — `online`, `counter` or `aggregator`.
    `courier` narrows to one carrier: an aggregator channel code matches on
    `aggregator_channel`; a dispatch provider (`lalamove`, `noon_send`, …)
    matches on the order's delivery record.
    """
    base_stmt = select(Order)

    if channel == "counter":
        base_stmt = base_stmt.where(Order.source == OrderSourceEnum.CASHIER.value)
    elif channel == "online":
        base_stmt = base_stmt.where(Order.source == OrderSourceEnum.ONLINE.value)
    elif channel == "aggregator":
        base_stmt = base_stmt.where(Order.source == OrderSourceEnum.AGGREGATOR.value)

    if courier:
        if courier in courier_catalog.AGGREGATOR_CODES:
            # `aggregator_channel` holds a marketplace display name ("Talabat",
            # "Keeta 2.0"); match the family by prefix.
            prefix = _AGGREGATOR_CHANNEL_PREFIX.get(courier, courier)
            base_stmt = base_stmt.where(
                Order.source == OrderSourceEnum.AGGREGATOR.value,
                Order.aggregator_channel.ilike(f"{prefix}%"),
            )
        else:
            base_stmt = base_stmt.where(
                exists().where(
                    OrderDelivery.order_id == Order.id,
                    OrderDelivery.provider == courier,
                )
            )
    if branch_id is not None:
        base_stmt = base_stmt.where(Order.branch_id == branch_id)

    if status:
        base_stmt = base_stmt.where(Order.status == status)
    if search:
        base_stmt = base_stmt.where(
            search_text.contains(Order.order_number, search)
            | search_text.contains(Order.email, search)
            | search_text.contains(Order.customer_name, search)
            | search_text.contains(Order.customer_phone, search)
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
