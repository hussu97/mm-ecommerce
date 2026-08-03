from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.cart import Cart, CartItem
from app.models.product import Product
from app.schemas.cart import (
    CartItemCreate,
    CartItemResponse,
    CartItemUpdate,
    CartResponse,
    SelectedOption,
)
from app.services import modifier_rules
from app.services.storefront_visibility import (
    is_website_product_visible,
    website_product_visibility_clause,
)

__all__ = [
    "add_item",
    "option_charge",
    "clear",
    "find_cart",
    "get_or_create",
    "merge",
    "remove_item",
    "update_item",
]


def _cart_load_options():
    return [
        selectinload(Cart.items)
        .joinedload(CartItem.product)
        .joinedload(Product.category)
    ]


async def _discard_hidden_items(db: AsyncSession, cart: Cart) -> Cart:
    """Remove items that were hidden after a shopper had added them."""
    hidden_items = [
        item for item in cart.items if not is_website_product_visible(item.product)
    ]
    if not hidden_items:
        return cart

    for item in hidden_items:
        await db.delete(item)
    await db.flush()
    return await _load_cart(db, cart.id)


def option_charge(option: dict) -> Decimal:
    """
    What one snapshot row adds to the unit price.

    `charged_total` is authoritative where it exists — it already has the
    group's free allowance taken off. Rows written before quantities existed
    carry neither key and repeated the option instead, so falling back to a
    single unit price prices them exactly as they were priced then.
    """
    if option.get("charged_total") is not None:
        return Decimal(str(option["charged_total"]))
    return Decimal(str(option.get("option_price", 0))) * int(
        option.get("quantity") or 1
    )


async def _build_response(cart: Cart) -> CartResponse:
    """Build a CartResponse with computed fields from an already-loaded Cart."""
    items: list[CartItemResponse] = []
    subtotal = Decimal("0.00")
    item_count = 0

    for cart_item in cart.items:
        product = cart_item.product

        # Compute unit price from stored JSONB snapshot
        selected_options = cart_item.selected_options or []
        options_total = sum(
            (option_charge(opt) for opt in selected_options), Decimal("0")
        )
        base = Decimal(str(product.base_price)) if product else Decimal("0")
        unit_price = base + options_total
        line_total = unit_price * cart_item.quantity
        subtotal += line_total
        item_count += cart_item.quantity

        item_resp = CartItemResponse.model_validate(cart_item)
        item_resp.product_name = product.name if product else None
        item_resp.product_image = (
            product.image_urls[0] if product and product.image_urls else None
        )
        item_resp.product_translations = product.translations or {} if product else {}
        item_resp.unit_price = float(unit_price)
        item_resp.line_total = float(line_total)

        items.append(item_resp)

    response = CartResponse.model_validate(cart)
    response.items = items
    response.subtotal = float(subtotal)
    response.item_count = item_count
    return response


async def _load_cart(db: AsyncSession, cart_id: uuid.UUID) -> Cart:
    stmt = select(Cart).options(*_cart_load_options()).where(Cart.id == cart_id)
    result = await db.execute(stmt)
    cart = result.scalar_one_or_none()
    if not cart:
        raise NotFoundError("Cart not found")
    return cart


async def _build_options_snapshot(
    db: AsyncSession,
    product: Product,
    selected_options: list[SelectedOption],
) -> list[dict]:
    """
    Validate the shopper's choices and snapshot them onto the cart line.

    The rules themselves live in `modifier_rules`, shared with the register,
    so a mixed box that a cashier can ring up is one a shopper can order and
    the price is the same number on both. What stays here is the shape of the
    snapshot, which the storefront reads and older rows already use.
    """
    if not selected_options:
        # Still worth resolving: a product with a required group must not
        # reach the basket with nothing chosen.
        resolved = await modifier_rules.resolve(db, product=product, selections=[])
    else:
        resolved = await modifier_rules.resolve(
            db,
            product=product,
            selections=[
                modifier_rules.Selection(
                    option_id=sel.option_id,
                    quantity=sel.quantity,
                    modifier_id=sel.modifier_id,
                )
                for sel in selected_options
            ],
        )

    return [
        {
            "modifier_id": str(choice.modifier_id),
            "modifier_name": choice.modifier_name,
            "modifier_translations": choice.modifier_translations,
            "option_id": str(choice.option_id),
            # The same id under the name the order pipeline and the inventory
            # depletion look for. Web orders used to carry only `option_id`,
            # so an option with its own recipe never moved any stock.
            "modifier_option_id": str(choice.option_id),
            "option_name": choice.option_name,
            "option_translations": choice.option_translations,
            # Per unit. Lines written before quantities existed repeated the
            # option instead, and have no `quantity` key — reading it as 1
            # keeps those baskets priced exactly as they were.
            "option_price": float(choice.unit_price),
            "quantity": choice.quantity,
            #: What the line is actually charged for this option, after the
            #: group's free allowance. Stored rather than recomputed so a
            #: basket priced under one set of rules keeps its price.
            "charged_total": float(choice.charged_total),
        }
        for choice in resolved
    ]


def _options_key(product_id: uuid.UUID, selected_options: list[dict]) -> tuple:
    """
    Dedup key: the product plus exactly what was chosen, quantities included.

    Two brownie boxes are the same line only if they hold the same brownies in
    the same numbers. Keying on the option ids alone would merge a box of two
    Ferrero and one Fudge into a box of one Ferrero and two Fudge.
    """
    counted: dict[str, int] = {}
    for opt in selected_options:
        key = str(opt["option_id"])
        counted[key] = counted.get(key, 0) + int(opt.get("quantity") or 1)
    return (product_id, tuple(sorted(counted.items())))


async def get_or_create(
    db: AsyncSession,
    user_id: uuid.UUID | None = None,
    session_id: str | None = None,
) -> CartResponse:
    if not user_id and not session_id:
        raise BadRequestError("Either user_id or session_id is required")

    # Try to find existing cart
    if user_id:
        stmt = (
            select(Cart).options(*_cart_load_options()).where(Cart.user_id == user_id)
        )
    else:
        stmt = (
            select(Cart)
            .options(*_cart_load_options())
            .where(Cart.session_id == session_id)
        )

    result = await db.execute(stmt)
    cart = result.scalar_one_or_none()

    if not cart:
        cart = Cart(user_id=user_id, session_id=session_id)
        db.add(cart)
        await db.flush()
        # Reload with options
        cart = await _load_cart(db, cart.id)

    cart = await _discard_hidden_items(db, cart)
    return await _build_response(cart)


async def add_item(
    db: AsyncSession,
    user_id: uuid.UUID | None,
    session_id: str | None,
    data: CartItemCreate,
) -> CartResponse:
    # Get or create cart
    if user_id:
        stmt = select(Cart).where(Cart.user_id == user_id)
    else:
        stmt = select(Cart).where(Cart.session_id == session_id)

    result = await db.execute(stmt)
    cart = result.scalar_one_or_none()
    if not cart:
        cart = Cart(user_id=user_id, session_id=session_id)
        db.add(cart)
        await db.flush()

    # Shoppers can buy only live website products in live categories. This is
    # intentionally enforced here too, rather than trusting the product grid.
    product_result = await db.execute(
        select(Product).where(
            Product.id == data.product_id,
            *website_product_visibility_clause(),
        )
    )
    product = product_result.scalar_one_or_none()
    if not product:
        raise NotFoundError(
            f"Product '{data.product_id}' is not available on the website"
        )

    # Block out-of-stock items
    if product.is_stock_product and product.stock_quantity <= 0:
        raise BadRequestError(f"Product '{product.name}' is out of stock")

    # Build + validate options snapshot
    options_snapshot = await _build_options_snapshot(db, product, data.selected_options)

    # Check for duplicate: same product + same sorted options
    existing_items_result = await db.execute(
        select(CartItem).where(
            CartItem.cart_id == cart.id, CartItem.product_id == data.product_id
        )
    )
    existing_items = existing_items_result.scalars().all()

    new_key = _options_key(data.product_id, options_snapshot)
    duplicate = None
    for item in existing_items:
        item_key = _options_key(data.product_id, item.selected_options or [])
        if item_key == new_key:
            duplicate = item
            break

    if duplicate:
        new_qty = duplicate.quantity + data.quantity
        if product.is_stock_product and new_qty > product.stock_quantity:
            new_qty = product.stock_quantity
        duplicate.quantity = new_qty
    else:
        quantity = data.quantity
        if product.is_stock_product:
            quantity = min(quantity, product.stock_quantity)
        item = CartItem(
            cart_id=cart.id,
            product_id=data.product_id,
            quantity=quantity,
            selected_options=options_snapshot,
        )
        db.add(item)

    await db.flush()
    cart = await _discard_hidden_items(db, await _load_cart(db, cart.id))
    return await _build_response(cart)


async def update_item(
    db: AsyncSession,
    user_id: uuid.UUID | None,
    session_id: str | None,
    item_id: uuid.UUID,
    data: CartItemUpdate,
) -> CartResponse:
    cart = await _get_user_cart(db, user_id, session_id)

    item_result = await db.execute(
        select(CartItem).where(CartItem.id == item_id, CartItem.cart_id == cart.id)
    )
    item = item_result.scalar_one_or_none()
    if not item:
        raise NotFoundError("Cart item not found")

    item.quantity = data.quantity
    await db.flush()

    cart = await _load_cart(db, cart.id)
    return await _build_response(cart)


async def remove_item(
    db: AsyncSession,
    user_id: uuid.UUID | None,
    session_id: str | None,
    item_id: uuid.UUID,
) -> CartResponse:
    cart = await _get_user_cart(db, user_id, session_id)

    item_result = await db.execute(
        select(CartItem).where(CartItem.id == item_id, CartItem.cart_id == cart.id)
    )
    item = item_result.scalar_one_or_none()
    if not item:
        raise NotFoundError("Cart item not found")

    await db.delete(item)
    await db.flush()

    cart = await _load_cart(db, cart.id)
    return await _build_response(cart)


async def clear(
    db: AsyncSession,
    user_id: uuid.UUID | None,
    session_id: str | None,
) -> CartResponse:
    cart = await _get_user_cart(db, user_id, session_id)

    items_result = await db.execute(select(CartItem).where(CartItem.cart_id == cart.id))
    for item in items_result.scalars().all():
        await db.delete(item)

    await db.flush()
    cart = await _load_cart(db, cart.id)
    return await _build_response(cart)


async def merge(
    db: AsyncSession, guest_session_id: str, user_id: uuid.UUID
) -> CartResponse:
    """Merge guest session cart into user cart after login."""
    # Find guest cart
    guest_result = await db.execute(
        select(Cart).where(Cart.session_id == guest_session_id)
    )
    guest_cart = guest_result.scalar_one_or_none()

    # Find or create user cart
    user_result = await db.execute(select(Cart).where(Cart.user_id == user_id))
    user_cart = user_result.scalar_one_or_none()
    if not user_cart:
        user_cart = Cart(user_id=user_id)
        db.add(user_cart)
        await db.flush()

    if not guest_cart:
        cart = await _load_cart(db, user_cart.id)
        return await _build_response(cart)

    # Load guest items
    guest_items_result = await db.execute(
        select(CartItem).where(CartItem.cart_id == guest_cart.id)
    )
    guest_items = guest_items_result.scalars().all()

    # Pre-load all user cart items and all needed products — 2 flat queries total
    user_items_result = await db.execute(
        select(CartItem).where(CartItem.cart_id == user_cart.id)
    )
    all_user_items = list(user_items_result.scalars().all())

    guest_product_ids = list({item.product_id for item in guest_items})
    products_result = await db.execute(
        select(Product).where(
            Product.id.in_(guest_product_ids), *website_product_visibility_clause()
        )
    )
    products_by_id = {p.id: p for p in products_result.scalars().all()}

    for guest_item in guest_items:
        # Check if user cart already has same product + options — no DB hit
        guest_key = _options_key(
            guest_item.product_id, guest_item.selected_options or []
        )
        existing = None
        for ui in all_user_items:
            if (
                ui.product_id == guest_item.product_id
                and _options_key(ui.product_id, ui.selected_options or []) == guest_key
            ):
                existing = ui
                break

        # Determine merged quantity, capped at stock if applicable
        if existing:
            merged_qty = existing.quantity + guest_item.quantity
        else:
            merged_qty = guest_item.quantity

        # Cap at available stock for stock-tracked products — no DB hit
        product = products_by_id.get(guest_item.product_id)
        if not product:
            continue
        if product and product.is_stock_product and merged_qty > product.stock_quantity:
            merged_qty = product.stock_quantity

        # Skip items with no available stock — don't create 0-quantity cart items
        if merged_qty <= 0:
            continue

        if existing:
            existing.quantity = merged_qty
        else:
            new_item = CartItem(
                cart_id=user_cart.id,
                product_id=guest_item.product_id,
                quantity=merged_qty,
                selected_options=guest_item.selected_options,
            )
            db.add(new_item)

    # Delete guest cart (cascade deletes items)
    await db.delete(guest_cart)
    await db.flush()

    cart = await _discard_hidden_items(db, await _load_cart(db, user_cart.id))
    return await _build_response(cart)


async def find_cart(
    db: AsyncSession, user_id: uuid.UUID | None, session_id: str | None
) -> Cart | None:
    """
    The basket this request belongs to, or nothing.

    Same precedence as checkout — the signed-in cart first, the session cart as
    a fallback for a guest who has only just been handed a token. Unlike
    `_get_user_cart` it stays quiet when there is no basket, because pricing a
    delivery for someone who has not added anything yet is ordinary, not an
    error.
    """
    if user_id:
        result = await db.execute(select(Cart).where(Cart.user_id == user_id))
        cart = result.scalar_one_or_none()
        if cart:
            return cart
    if session_id:
        result = await db.execute(select(Cart).where(Cart.session_id == session_id))
        return result.scalar_one_or_none()
    return None


async def _get_user_cart(
    db: AsyncSession, user_id: uuid.UUID | None, session_id: str | None
) -> Cart:
    """Get a cart without loading relationships."""
    if user_id:
        stmt = select(Cart).where(Cart.user_id == user_id)
    elif session_id:
        stmt = select(Cart).where(Cart.session_id == session_id)
    else:
        raise BadRequestError("Either user_id or session_id is required")

    result = await db.execute(stmt)
    cart = result.scalar_one_or_none()
    if not cart:
        raise NotFoundError("Cart not found")
    return cart
