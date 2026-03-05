from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.cart import Cart, CartItem
from app.models.product import Product, ProductVariant
from app.schemas.cart import CartItemCreate, CartItemResponse, CartItemUpdate, CartResponse


def _cart_load_options():
    return [
        selectinload(Cart.items).joinedload(CartItem.variant).joinedload(ProductVariant.product)
    ]


async def _build_response(cart: Cart) -> CartResponse:
    """Build a CartResponse with computed fields from an already-loaded Cart."""
    items: list[CartItemResponse] = []
    subtotal = Decimal("0.00")
    item_count = 0

    for cart_item in cart.items:
        variant = cart_item.variant
        product = variant.product if variant else None

        line_total = (variant.price * cart_item.quantity) if variant else None
        if line_total:
            subtotal += line_total

        item_count += cart_item.quantity

        item_resp = CartItemResponse.model_validate(cart_item)
        item_resp.product_name = product.name if product else None
        item_resp.product_image = (product.image_urls[0] if product and product.image_urls else None)
        item_resp.line_total = line_total

        items.append(item_resp)

    response = CartResponse.model_validate(cart)
    response.items = items
    response.subtotal = subtotal
    response.item_count = item_count
    return response


async def _load_cart(db: AsyncSession, cart_id: uuid.UUID) -> Cart:
    stmt = (
        select(Cart)
        .options(*_cart_load_options())
        .where(Cart.id == cart_id)
    )
    result = await db.execute(stmt)
    cart = result.scalar_one_or_none()
    if not cart:
        raise NotFoundError("Cart not found")
    return cart


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
            select(Cart)
            .options(*_cart_load_options())
            .where(Cart.user_id == user_id)
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
    # Get or create cart (without loading relationships for efficiency)
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

    # Validate variant exists and has stock
    variant_result = await db.execute(
        select(ProductVariant).where(
            ProductVariant.id == data.variant_id, ProductVariant.is_active == True  # noqa: E712
        )
    )
    variant = variant_result.scalar_one_or_none()
    if not variant:
        raise NotFoundError(f"Product variant '{data.variant_id}' not found or inactive")

    if variant.stock_quantity < data.quantity:
        raise BadRequestError(
            f"Insufficient stock. Only {variant.stock_quantity} available."
        )

    # Check if item already in cart
    existing_item_result = await db.execute(
        select(CartItem).where(
            CartItem.cart_id == cart.id, CartItem.variant_id == data.variant_id
        )
    )
    existing_item = existing_item_result.scalar_one_or_none()

    if existing_item:
        new_qty = existing_item.quantity + data.quantity
        if variant.stock_quantity < new_qty:
            raise BadRequestError(
                f"Insufficient stock. Only {variant.stock_quantity} available."
            )
        existing_item.quantity = new_qty
    else:
        item = CartItem(cart_id=cart.id, variant_id=data.variant_id, quantity=data.quantity)
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

    # Check stock
    variant_result = await db.execute(
        select(ProductVariant).where(ProductVariant.id == item.variant_id)
    )
    variant = variant_result.scalar_one_or_none()
    if variant and variant.stock_quantity < data.quantity:
        raise BadRequestError(f"Insufficient stock. Only {variant.stock_quantity} available.")

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


async def merge(db: AsyncSession, guest_session_id: str, user_id: uuid.UUID) -> CartResponse:
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
        # Nothing to merge
        cart = await _load_cart(db, user_cart.id)
        return await _build_response(cart)

    # Load guest items
    guest_items_result = await db.execute(
        select(CartItem).where(CartItem.cart_id == guest_cart.id)
    )
    guest_items = guest_items_result.scalars().all()

    for guest_item in guest_items:
        # Check if user cart already has same variant
        existing_result = await db.execute(
            select(CartItem).where(
                CartItem.cart_id == user_cart.id,
                CartItem.variant_id == guest_item.variant_id,
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            # Fetch variant stock to cap merged quantity
            variant_result = await db.execute(
                select(ProductVariant).where(ProductVariant.id == guest_item.variant_id)
            )
            variant = variant_result.scalar_one_or_none()
            stock = variant.stock_quantity if variant else 0
            new_qty = min(existing.quantity + guest_item.quantity, stock)
            existing.quantity = new_qty
        else:
            new_item = CartItem(
                cart_id=user_cart.id,
                variant_id=guest_item.variant_id,
                quantity=guest_item.quantity,
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
