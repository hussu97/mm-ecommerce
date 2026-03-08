from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.cart import Cart, CartItem
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.product import Product
from app.schemas.cart import (
    CartItemCreate,
    CartItemResponse,
    CartItemUpdate,
    CartResponse,
    SelectedOption,
)


def _cart_load_options():
    return [selectinload(Cart.items).joinedload(CartItem.product)]


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
            Decimal(str(opt.get("option_price", 0))) for opt in selected_options
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
    """Validate selected options against product modifiers and build JSONB snapshot."""
    if not selected_options:
        return []

    # Load product modifiers with options
    pm_result = await db.execute(
        select(ProductModifier)
        .where(ProductModifier.product_id == product.id)
        .options(joinedload(ProductModifier.modifier).selectinload(Modifier.options))
    )
    product_modifiers = pm_result.scalars().unique().all()

    # Group selected options by modifier_id
    selections_by_modifier: dict[uuid.UUID, list[uuid.UUID]] = {}
    for sel in selected_options:
        selections_by_modifier.setdefault(sel.modifier_id, []).append(sel.option_id)

    # Validate constraints for each modifier
    for pm in product_modifiers:
        mod_selections = selections_by_modifier.get(pm.modifier_id, [])
        count = len(mod_selections)
        if count < pm.minimum_options:
            raise BadRequestError(
                f"Modifier '{pm.modifier.name}' requires at least {pm.minimum_options} option(s), got {count}"
            )
        if count > pm.maximum_options:
            raise BadRequestError(
                f"Modifier '{pm.modifier.name}' allows at most {pm.maximum_options} option(s), got {count}"
            )
        # Unique enforcement: stored flag OR single-pick shorthand (min==max==1)
        effective_unique = pm.unique_options or (
            pm.minimum_options == 1 and pm.maximum_options == 1
        )
        if effective_unique and len(set(mod_selections)) < len(mod_selections):
            raise BadRequestError(
                f"Modifier '{pm.modifier.name}' does not allow duplicate options"
            )

    # Build snapshot
    snapshot = []
    option_id_map: dict[uuid.UUID, ModifierOption] = {}
    modifier_map: dict[uuid.UUID, Modifier] = {}
    for pm in product_modifiers:
        modifier_map[pm.modifier_id] = pm.modifier
        for opt in pm.modifier.options:
            option_id_map[opt.id] = opt

    for sel in selected_options:
        opt = option_id_map.get(sel.option_id)
        if not opt:
            raise BadRequestError(
                f"Option '{sel.option_id}' not found for this product"
            )
        mod = modifier_map.get(sel.modifier_id)
        snapshot.append(
            {
                "modifier_id": str(sel.modifier_id),
                "modifier_name": mod.name if mod else "",
                "modifier_translations": mod.translations if mod else {},
                "option_id": str(sel.option_id),
                "option_name": opt.name,
                "option_translations": opt.translations or {},
                "option_price": float(opt.price),
            }
        )

    return snapshot


def _options_key(product_id: uuid.UUID, selected_options: list[dict]) -> tuple:
    """Dedup key: product_id + sorted option_ids."""
    option_ids = tuple(sorted(opt["option_id"] for opt in selected_options))
    return (product_id, option_ids)


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

    # Validate product exists and is active
    product_result = await db.execute(
        select(Product).where(
            Product.id == data.product_id,
            Product.is_active == True,  # noqa: E712
        )
    )
    product = product_result.scalar_one_or_none()
    if not product:
        raise NotFoundError(f"Product '{data.product_id}' not found or inactive")

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
        duplicate.quantity += data.quantity
    else:
        item = CartItem(
            cart_id=cart.id,
            product_id=data.product_id,
            quantity=data.quantity,
            selected_options=options_snapshot,
        )
        db.add(item)

    await db.flush()
    cart = await _load_cart(db, cart.id)
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
        select(Product).where(Product.id.in_(guest_product_ids))
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
        if product and product.is_stock_product and merged_qty > product.stock_quantity:
            merged_qty = product.stock_quantity

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

    cart = await _load_cart(db, user_cart.id)
    return await _build_response(cart)


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
