from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.cart import Cart, CartItem
from app.models.order import DeliveryMethodEnum, Order, OrderItem, OrderStatusEnum
from app.models.promo_code import PromoCode
from app.schemas.order import OrderCreate, OrderListResponse, OrderResponse
from app.services import delivery_service, promo_code_service


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
    OrderStatusEnum.CONFIRMED: {OrderStatusEnum.PACKED, OrderStatusEnum.CANCELLED},
    OrderStatusEnum.PACKED: set(),
    OrderStatusEnum.CANCELLED: set(),
}


def _order_load_options():
    return [selectinload(Order.items)]


async def _generate_order_number(db: AsyncSession) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"MM-{today}-"

    result = await db.execute(
        select(Order.order_number)
        .where(Order.order_number.like(f"{prefix}%"))
        .order_by(Order.order_number.desc())
        .limit(1)
        .with_for_update()
    )
    last = result.scalar_one_or_none()

    if last:
        last_seq = int(last.split("-")[-1])
        seq = last_seq + 1
    else:
        seq = 1

    return f"{prefix}{seq:03d}"


async def create_order(
    db: AsyncSession,
    data: OrderCreate,
    user_id: uuid.UUID | None,
) -> OrderResponse:
    # ── 1. Locate cart ──────────────────────────────────────────────────────
    if user_id:
        cart_stmt = select(Cart).where(Cart.user_id == user_id)
    elif data.session_id:
        cart_stmt = select(Cart).where(Cart.session_id == data.session_id)
    else:
        raise BadRequestError("No cart found — provide session_id for guest checkout")

    cart_result = await db.execute(
        cart_stmt.options(selectinload(Cart.items).joinedload(CartItem.product))
    )
    cart = cart_result.scalar_one_or_none()
    if not cart or not cart.items:
        raise BadRequestError("Cart is empty")

    # ── 2. Validate delivery requirements ───────────────────────────────────
    if (
        data.delivery_method == DeliveryMethodEnum.DELIVERY
        and not data.shipping_address
    ):
        raise BadRequestError("Shipping address is required for delivery orders")

    # ── 3. Compute subtotal from cart items ─────────────────────────────────
    subtotal = Decimal("0.00")
    items_data: list[dict] = []

    for cart_item in cart.items:
        product = cart_item.product
        if not product or not product.is_active:
            raise BadRequestError("A product in your cart is no longer available")

        selected_options = cart_item.selected_options or []
        options_price = sum(
            Decimal(str(opt.get("option_price", 0))) for opt in selected_options
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

    # ── 4. Apply promo code ──────────────────────────────────────────────────
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

    # ── 5. Calculate delivery fee ────────────────────────────────────────────
    discounted_subtotal = subtotal - discount_amount
    region = data.shipping_address.region if data.shipping_address else None
    delivery_fee = delivery_service.calculate_fee(
        data.delivery_method, region, discounted_subtotal
    )

    # ── 6. Final total ───────────────────────────────────────────────────────
    total = discounted_subtotal + delivery_fee

    # ── 6b. VAT back-calculation (goods only; delivery excluded) ─────────────
    VAT_RATE = Decimal("0.05")
    taxable = subtotal - discount_amount
    vat_amount = (taxable * VAT_RATE / (1 + VAT_RATE)).quantize(Decimal("0.01"))
    total_excl_vat = (taxable / (1 + VAT_RATE)).quantize(Decimal("0.01"))

    # ── 7. Build address snapshot ────────────────────────────────────────────
    address_snapshot: dict | None = None
    if data.shipping_address:
        address_snapshot = data.shipping_address.model_dump(mode="json")

    # ── 8. Generate order number ─────────────────────────────────────────────
    order_number = await _generate_order_number(db)

    # ── 9. Persist order ─────────────────────────────────────────────────────
    order = Order(
        order_number=order_number,
        user_id=user_id,
        email=data.email,
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
    db.add(order)
    await db.flush()

    # ── 10. Create order items ────────────────────────────────────────────────
    for item in items_data:
        order_item = OrderItem(order_id=order.id, **item)
        db.add(order_item)

    # ── 11. Increment promo uses (atomic) ────────────────────────────────────
    if promo_obj:
        if promo_obj.max_uses is not None:
            promo_result = await db.execute(
                sql_update(PromoCode)
                .where(
                    PromoCode.id == promo_obj.id,
                    PromoCode.current_uses < PromoCode.max_uses,
                )
                .values(current_uses=PromoCode.current_uses + 1)
                .returning(PromoCode.id)
                .execution_options(synchronize_session=False)
            )
            if not promo_result.scalar_one_or_none():
                raise BadRequestError("Promo code has reached its usage limit")
        else:
            await db.execute(
                sql_update(PromoCode)
                .where(PromoCode.id == promo_obj.id)
                .values(current_uses=PromoCode.current_uses + 1)
                .execution_options(synchronize_session=False)
            )

    # ── 12. Clear cart ────────────────────────────────────────────────────────
    items_result = await db.execute(select(CartItem).where(CartItem.cart_id == cart.id))
    for ci in items_result.scalars().all():
        await db.delete(ci)

    await db.flush()

    # Reload with items
    stmt = select(Order).options(*_order_load_options()).where(Order.id == order.id)
    result = await db.execute(stmt)
    order = result.scalar_one()
    return OrderResponse.model_validate(order)


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

    if not admin and user_id and order.user_id != user_id:
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

    await db.flush()
    await db.refresh(order)

    # Reload items
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
