"""
POS order lifecycle.

    open → add/void lines → send to kitchen → discount/charge → pay → close

Totals are never trusted from the client. Every mutation re-prices the whole
order through `pos_pricing.calculate_order`, so a terminal that goes offline
mid-edit and replays its queue cannot drift the bill.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.business_settings import BusinessSettings
from app.models.charge import Charge
from app.models.kitchen_flow import KitchenFlow
from app.models.order import Order, OrderItem
from app.models.payment_method import PaymentMethod, PaymentMethodTypeEnum
from app.models.pos_order import (
    DiscountSourceEnum,
    KitchenTicket,
    KitchenTicketItem,
    KitchenTicketStatusEnum,
    OrderCharge,
    OrderDiscount,
    OrderItemStatusEnum,
    OrderPayment,
    OrderSourceEnum,
    OrderTax,
    OrderTypeEnum,
    PosOrderStatusEnum,
)
from app.models.pos_table import PosTable, TableStatusEnum
from app.models.product import Product
from app.models.tax import TaxGroup
from app.models.till import DrawerOperationTypeEnum, Till
from app.models.user import User
from app.services import (
    business_day_service,
    inventory_service,
    modifier_rules,
    pos_pricing,
    till_service,
)
from app.services.pos_pricing import (
    ChargeInput,
    DiscountInput,
    LineInput,
    money,
)

__all__ = [
    "add_item",
    "apply_discount",
    "apply_charge",
    "close_order",
    "get_order",
    "open_order",
    "record_payment",
    "recalculate",
    "send_to_kitchen",
    "void_item",
    "void_order",
]

logger = logging.getLogger(__name__)

OPEN_STATUSES = {
    PosOrderStatusEnum.DRAFT.value,
    PosOrderStatusEnum.PENDING.value,
    PosOrderStatusEnum.ACTIVE.value,
}

LIVE_ITEM_STATUSES = {
    OrderItemStatusEnum.PENDING.value,
    OrderItemStatusEnum.ACTIVE.value,
    OrderItemStatusEnum.CLOSED.value,
}


# ─── Loading ──────────────────────────────────────────────────────────────────


async def get_order(db: AsyncSession, order_id: uuid.UUID) -> Order:
    stmt = (
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.items),
            selectinload(Order.payments),
            selectinload(Order.order_charges),
            selectinload(Order.order_discounts),
            selectinload(Order.order_taxes),
        )
        # Without this the identity map hands back the order with whatever
        # collections it was first loaded with, so a line added moments ago is
        # invisible and the recalculated totals lag one operation behind.
        .execution_options(populate_existing=True)
    )
    order = (await db.execute(stmt)).scalars().unique().one_or_none()
    if order is None:
        raise NotFoundError("Order not found")
    return order


def _assert_open(order: Order) -> None:
    if order.pos_status not in OPEN_STATUSES:
        raise ConflictError(
            f"Order is {order.pos_status} and can no longer be modified"
        )


def _net_paid(order: Order) -> Decimal:
    """What the shop is still holding for this order, refunds netted off."""
    return money(
        sum(
            (
                (-money(p.amount) if p.is_refund else money(p.amount))
                for p in order.payments
            ),
            Decimal("0"),
        )
    )


# ─── Opening ──────────────────────────────────────────────────────────────────


async def _next_check_number(
    db: AsyncSession, branch_id: uuid.UUID, business_date: str, reset_daily: bool
) -> int:
    """
    Per-branch check number. Resets each trading day when configured, which is
    what customers expect ("order 12" rather than "order 128,431").
    """
    stmt = select(func.max(Order.check_number)).where(Order.branch_id == branch_id)
    if reset_daily:
        stmt = stmt.where(Order.business_date == business_date)
    current = (await db.execute(stmt)).scalar_one_or_none()
    return int(current or 0) + 1


async def _settings(db: AsyncSession) -> BusinessSettings:
    existing = (await db.execute(select(BusinessSettings).limit(1))).scalars().first()
    if existing is None:
        existing = BusinessSettings()
        db.add(existing)
        await db.flush()
    return existing


async def open_order(
    db: AsyncSession,
    *,
    branch: Branch,
    user: User,
    order_type: str,
    till: Till | None = None,
    device_id: uuid.UUID | None = None,
    table_id: uuid.UUID | None = None,
    guests: int = 1,
    customer_id: uuid.UUID | None = None,
    customer_name: str | None = None,
    customer_phone: str | None = None,
    notes: str | None = None,
    source: str = OrderSourceEnum.CASHIER.value,
    due_at=None,
    region_id=None,
) -> Order:
    if order_type not in {t.value for t in OrderTypeEnum}:
        raise BadRequestError(f"Unknown order type '{order_type}'")

    settings = await _settings(db)
    day = await business_day_service.get_or_open(db, branch, opened_by=user)
    check_number = await _next_check_number(
        db, branch.id, day.business_date, settings.order_number_reset_daily
    )

    if order_type == OrderTypeEnum.DINE_IN.value and table_id is not None:
        table = await db.get(PosTable, table_id)
        if table is None:
            raise BadRequestError("Table not found")
        if table.status == TableStatusEnum.OCCUPIED.value:
            raise ConflictError(f"Table {table.name} already has an open check")
        table.status = TableStatusEnum.OCCUPIED.value

    order = Order(
        order_number=f"POS-{branch.reference}-{day.business_date}-{check_number:04d}",
        user_id=customer_id,
        email="",  # POS walk-ins have no email; kept non-null for the web schema
        delivery_method="pickup" if order_type != "delivery" else "delivery",
        delivery_fee=Decimal("0"),
        subtotal=Decimal("0"),
        discount_amount=Decimal("0"),
        total=Decimal("0"),
        status="created",
        is_pos=True,
        branch_id=branch.id,
        table_id=table_id,
        region_id=region_id,
        device_id=device_id,
        till_id=till.id if till else None,
        order_type=order_type,
        source=source,
        pos_status=PosOrderStatusEnum.ACTIVE.value,
        business_date=day.business_date,
        check_number=check_number,
        guests=max(guests, 1),
        creator_id=user.id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        notes=notes,
        # Ahead orders: when the customer wants it, not when it was rung up.
        due_at=due_at,
        opened_at=utcnow(),
    )
    db.add(order)
    await db.flush()
    await db.refresh(order)
    return await get_order(db, order.id)


# ─── Lines ────────────────────────────────────────────────────────────────────


async def _resolve_tax(
    db: AsyncSession, tax_group_id: uuid.UUID | None
) -> tuple[Decimal, str, str | None, bool]:
    """Return (rate, name, tax_id, is_inclusive) for a tax group."""
    if tax_group_id is None:
        return Decimal("0"), "No tax", None, True
    group = (
        (
            await db.execute(
                select(TaxGroup)
                .where(TaxGroup.id == tax_group_id)
                .options(selectinload(TaxGroup.taxes))
            )
        )
        .scalars()
        .unique()
        .one_or_none()
    )
    # A deactivated tax must not be charged. Without this filter, switching a
    # tax off leaves it silently summing into every order that uses its group —
    # which is how a duplicate 5% VAT row would have billed customers 10%.
    links = [link for link in (group.taxes if group else []) if link.tax.is_active]
    if group is None or not links:
        return Decimal("0"), "No tax", None, True
    # Groups in practice carry a single rate; combine if more are configured.
    first = links[0].tax
    rate = sum((Decimal(str(link.tax.rate)) for link in links), Decimal("0"))
    return rate, first.name, str(first.id), first.type == "inclusive"


async def add_item(
    db: AsyncSession,
    *,
    order: Order,
    user: User,
    product_id: uuid.UUID,
    quantity: int = 1,
    unit_price_override: Decimal | None = None,
    selected_options: list[dict] | None = None,
    kitchen_notes: str | None = None,
    course_id: uuid.UUID | None = None,
    weight: Decimal | None = None,
) -> OrderItem:
    _assert_open(order)
    if quantity < 1:
        raise BadRequestError("Quantity must be at least 1")

    product = await db.get(Product, product_id)
    if product is None:
        raise BadRequestError("Product not found")
    if not product.is_active:
        raise ConflictError(f"{product.name} is not available")

    # The terminal's choices are re-decided here rather than accepted. It sent
    # a price per option, and taking that number meant a device on a counter
    # could ring a paid extra up at zero and nothing in the books would look
    # wrong; it also never counted the picks against the group's minimum and
    # maximum, so a "Box of 3" could leave the till holding one brownie or
    # nine. Prices come from the catalogue and the rules are enforced for
    # every channel in one place — see `modifier_rules`.
    resolved = await modifier_rules.resolve(
        db,
        product=product,
        selections=[
            modifier_rules.Selection(
                option_id=uuid.UUID(str(o["modifier_option_id"])),
                quantity=int(o.get("quantity") or 1),
            )
            for o in (selected_options or [])
            if o.get("modifier_option_id")
        ],
    )
    options = [
        {
            "modifier_option_id": str(choice.option_id),
            "modifier_id": str(choice.modifier_id),
            "modifier_name": choice.modifier_name,
            "name": choice.option_name,
            "sku": choice.option_sku,
            # Per unit, so a receipt can print "2 × Ferrero  +8.00" without
            # having to divide, and `quantity` is what the kitchen reads.
            "price": float(money(choice.unit_price)),
            "quantity": choice.quantity,
        }
        for choice in resolved
    ]
    options_price = money(
        sum((choice.charged_total for choice in resolved), Decimal("0"))
    )

    is_open_price = unit_price_override is not None
    base_price = (
        money(unit_price_override) if is_open_price else money(product.base_price)
    )

    # Open price is a property of the product, not a per-line choice. Keeping
    # the two in step closes a hole at each end: a catalogue item priced by the
    # cashier ("Cake - Customer Specification") would otherwise ring up at
    # 0.00 if the price were simply left out, and an override on an ordinary
    # item would let anyone reprice the menu at the till, sidestepping the
    # discount rules and every report that reconciles against them.
    if product.pricing_method == "open" and not is_open_price:
        raise BadRequestError(f"{product.name} is sold at an open price — enter it")
    if is_open_price and product.pricing_method != "open":
        raise BadRequestError(f"{product.name} has a set price — use a discount")

    if product.is_sold_by_weight:
        if weight is None or weight <= 0:
            raise BadRequestError(
                f"{product.name} is sold by weight — enter the weight"
            )
        # The catalogue price is per kilo; what the customer pays is that
        # times what is on the scale. Quantity stays 1: the line is one
        # bag of brownies, not 0.4 of one.
        base_price = money(base_price * weight)
    elif weight is not None:
        raise BadRequestError(f"{product.name} is not sold by weight")
    unit_price = money(base_price + options_price)

    item = OrderItem(
        order_id=order.id,
        product_id=product.id,
        weight=weight,
        product_name=product.name,
        product_sku=product.sku or "",
        product_translations=product.translations or {},
        quantity=quantity,
        base_price=base_price,
        options_price=options_price,
        unit_price=unit_price,
        total_price=money(unit_price * quantity),
        selected_options_snapshot=options,
        status=OrderItemStatusEnum.ACTIVE.value,
        is_open_price=is_open_price,
        kitchen_notes=kitchen_notes,
        course_id=course_id,
        kitchen_flow_id=await _route_to_kitchen_flow(db, order, product),
        creator_id=user.id,
        added_at=utcnow(),
    )
    db.add(item)
    await db.flush()
    await recalculate(db, order)
    await db.refresh(item)
    return item


async def _route_to_kitchen_flow(
    db: AsyncSession, order: Order, product: Product
) -> uuid.UUID | None:
    """
    Pick the kitchen station for a line: the flow whose categories include the
    product's category, else the branch default so nothing is silently dropped.
    """
    if order.branch_id is None:
        return None
    stmt = (
        select(KitchenFlow)
        .where(
            KitchenFlow.branch_id == order.branch_id,
            KitchenFlow.is_active.is_(True),
            KitchenFlow.deleted_at.is_(None),
        )
        .options(selectinload(KitchenFlow.categories))
        .order_by(KitchenFlow.display_order)
    )
    flows = list((await db.execute(stmt)).scalars().unique().all())
    default_flow: KitchenFlow | None = None
    for flow in flows:
        if flow.is_default:
            default_flow = flow
        if flow.order_types and order.order_type not in flow.order_types:
            continue
        category_ids = {link.category_id for link in flow.categories}
        if product.category_id and product.category_id in category_ids:
            return flow.id
    return default_flow.id if default_flow else None


async def void_item(
    db: AsyncSession,
    *,
    order: Order,
    item_id: uuid.UUID,
    user: User,
    reason_id: uuid.UUID | None = None,
) -> Order:
    _assert_open(order)
    item = next((i for i in order.items if i.id == item_id), None)
    if item is None:
        raise NotFoundError("Order item not found")
    if item.status == OrderItemStatusEnum.VOID.value:
        raise ConflictError("This line is already void")

    item.status = OrderItemStatusEnum.VOID.value
    item.voided_by_id = user.id
    item.voided_at = utcnow()
    item.void_reason_id = reason_id
    await db.flush()
    return await recalculate(db, order)


async def return_item(
    db: AsyncSession,
    *,
    order: Order,
    item_id: uuid.UUID,
    quantity: int,
    user: User,
    reason_id: uuid.UUID | None = None,
) -> Order:
    """Return part or all of a line after the order is closed."""
    item = next((i for i in order.items if i.id == item_id), None)
    if item is None:
        raise NotFoundError("Order item not found")
    already = item.returned_quantity or 0
    if quantity < 1 or already + quantity > item.quantity:
        raise BadRequestError(
            f"Cannot return {quantity}; only {item.quantity - already} remain"
        )

    item.returned_quantity = already + quantity
    item.void_reason_id = reason_id
    item.voided_by_id = user.id
    if item.returned_quantity >= item.quantity:
        item.status = OrderItemStatusEnum.RETURNED.value
    await db.flush()
    return await recalculate(db, order)


# ─── Discounts and charges ────────────────────────────────────────────────────


async def apply_discount(
    db: AsyncSession,
    *,
    order: Order,
    user: User,
    name: str,
    is_percentage: bool,
    value: Decimal,
    source: str = DiscountSourceEnum.OPEN.value,
    order_item_id: uuid.UUID | None = None,
    reference_id: uuid.UUID | None = None,
) -> Order:
    _assert_open(order)
    if is_percentage and not (0 < value <= 1):
        raise BadRequestError("Percentage discounts are fractions between 0 and 1")
    if not is_percentage and value <= 0:
        raise BadRequestError("Discount amount must be positive")

    # One discount per scope — re-applying replaces rather than stacks, which is
    # what cashiers expect when they correct a mistake.
    for existing in list(order.order_discounts):
        if existing.order_item_id == order_item_id:
            await db.delete(existing)
    await db.flush()

    db.add(
        OrderDiscount(
            order_id=order.id,
            order_item_id=order_item_id,
            source=source,
            name=name,
            reference_id=reference_id,
            is_percentage=is_percentage,
            value=value,
            amount=Decimal("0"),  # filled by recalculate
            applied_by_id=user.id,
        )
    )
    await db.flush()
    return await recalculate(db, order)


async def remove_discount(
    db: AsyncSession, *, order: Order, discount_id: uuid.UUID
) -> Order:
    _assert_open(order)
    discount = next((d for d in order.order_discounts if d.id == discount_id), None)
    if discount is None:
        raise NotFoundError("Discount not found on this order")
    await db.delete(discount)
    await db.flush()
    return await recalculate(db, order)


async def apply_charge(
    db: AsyncSession,
    *,
    order: Order,
    charge_id: uuid.UUID | None = None,
    name: str | None = None,
    charge_type: str = "fixed",
    value: Decimal = Decimal("0"),
) -> Order:
    _assert_open(order)

    if charge_id is not None:
        charge = await db.get(Charge, charge_id)
        if charge is None or charge.deleted_at is not None:
            raise BadRequestError("Charge not found")
        if order.order_type and not charge.applies_to(order.order_type):
            raise ConflictError(
                f"{charge.name} does not apply to {order.order_type} orders"
            )
        name, charge_type, value = charge.name, charge.type, Decimal(str(charge.value))

    if not name:
        raise BadRequestError("An open charge needs a name")

    db.add(
        OrderCharge(
            order_id=order.id,
            charge_id=charge_id,
            name=name,
            type=charge_type,
            value=value,
            amount=Decimal("0"),  # filled by recalculate
        )
    )
    await db.flush()
    return await recalculate(db, order)


# ─── Repricing ────────────────────────────────────────────────────────────────


async def recalculate(db: AsyncSession, order: Order) -> Order:
    """
    Re-price the order from its lines and persist every derived total.

    This is the single writer of money on an order — no other function sets
    subtotal, tax or total, so the invariant
    `total_excl_tax + tax + rounding == total` holds by construction.
    """
    order = await get_order(db, order.id)
    settings = await _settings(db)

    live_items = [
        i
        for i in order.items
        if (i.status or OrderItemStatusEnum.ACTIVE.value)
        != OrderItemStatusEnum.VOID.value
    ]

    per_item_discounts: dict[uuid.UUID, list[OrderDiscount]] = {}
    order_level: OrderDiscount | None = None
    for discount in order.order_discounts:
        if discount.order_item_id is None:
            order_level = discount
        else:
            per_item_discounts.setdefault(discount.order_item_id, []).append(discount)

    tax_cache: dict[uuid.UUID | None, tuple[Decimal, str, str | None, bool]] = {}
    lines: list[LineInput] = []
    for item in live_items:
        product = await db.get(Product, item.product_id) if item.product_id else None
        tax_group_id = getattr(product, "tax_group_id", None) if product else None
        if tax_group_id not in tax_cache:
            tax_cache[tax_group_id] = await _resolve_tax(db, tax_group_id)
        rate, tax_name, tax_id, inclusive = tax_cache[tax_group_id]

        lines.append(
            LineInput(
                quantity=item.quantity,
                unit_price=Decimal(str(item.base_price)),
                options_price=Decimal(str(item.options_price)),
                tax_rate=rate,
                tax_is_inclusive=inclusive,
                tax_id=tax_id,
                tax_name=tax_name,
                returned_quantity=item.returned_quantity or 0,
                is_non_revenue=bool(product and product.is_non_revenue),
                discounts=[
                    DiscountInput(
                        name=d.name,
                        source=d.source,
                        is_percentage=d.is_percentage,
                        value=Decimal(str(d.value)),
                    )
                    for d in per_item_discounts.get(item.id, [])
                ],
            )
        )

    charge_inputs: list[ChargeInput] = []
    for charge in order.order_charges:
        rate, tax_name, tax_id, inclusive = tax_cache.get(
            None, (Decimal("0"), "No tax", None, True)
        )
        charge_inputs.append(
            ChargeInput(
                name=charge.name,
                type=charge.type,
                value=Decimal(str(charge.value)),
                tax_rate=rate,
                tax_is_inclusive=inclusive,
                tax_id=tax_id,
                tax_name=tax_name,
            )
        )

    totals = pos_pricing.calculate_order(
        lines,
        charges=charge_inputs,
        order_discount=(
            DiscountInput(
                name=order_level.name,
                source=order_level.source,
                is_percentage=order_level.is_percentage,
                value=Decimal(str(order_level.value)),
            )
            if order_level
            else None
        ),
        cash_rounding_step=Decimal(str(settings.cash_rounding_step or 0)),
    )

    # Write the computed amounts back onto the persisted rows.
    for item, line_total in zip(live_items, totals.lines):
        item.discount_amount = line_total.discount
        item.tax_amount = line_total.tax
        item.tax_exclusive_unit_price = line_total.unit_tax_exclusive
        item.tax_exclusive_total_price = line_total.tax_exclusive
        item.total_price = line_total.net_of_discount

    for item_id, discounts in per_item_discounts.items():
        item = next((i for i in live_items if i.id == item_id), None)
        if item is None:
            continue
        for discount in discounts:
            discount.amount = item.discount_amount
    if order_level is not None:
        order_level.amount = totals.order_discount

    for charge, amount in zip(order.order_charges, totals.charge_amounts):
        charge.amount = amount

    # Replace the tax breakdown wholesale — rates and lines may both have changed.
    for existing in list(order.order_taxes):
        await db.delete(existing)
    await db.flush()
    for tax_line in totals.taxes:
        db.add(
            OrderTax(
                order_id=order.id,
                tax_id=uuid.UUID(tax_line.tax_id) if tax_line.tax_id else None,
                name=tax_line.name,
                rate=tax_line.rate,
                taxable_amount=tax_line.taxable_amount,
                amount=tax_line.amount,
            )
        )

    order.subtotal = totals.subtotal
    order.discount_amount = totals.discount_total
    order.charges_amount = totals.charges
    order.vat_amount = totals.tax_total
    order.total_excl_vat = totals.total_excl_tax
    order.rounding_amount = totals.rounding
    order.total = totals.total

    await db.flush()
    return await get_order(db, order.id)


# ─── Kitchen ──────────────────────────────────────────────────────────────────


async def send_to_kitchen(
    db: AsyncSession, *, order: Order, course_id=None
) -> list[KitchenTicket]:
    """
    Fire every line not yet sent, grouped into one ticket per kitchen station.
    Re-firing is safe — already-sent lines are skipped.
    """
    _assert_open(order)
    pending = [
        i
        for i in order.items
        if i.sent_to_kitchen_at is None and i.status != OrderItemStatusEnum.VOID.value
    ]
    if course_id is not None:
        # Firing one course: starters now, mains when the table is ready.
        # Without this a single send dumps the whole check on the pass and the
        # mains go cold while the starters are being eaten.
        pending = [i for i in pending if i.course_id == course_id]
        if not pending:
            raise ConflictError("Nothing left to fire in that course")
    if not pending:
        raise ConflictError("Every line has already been sent to the kitchen")

    sequence = int(
        (
            await db.execute(
                select(func.count())
                .select_from(KitchenTicket)
                .where(KitchenTicket.order_id == order.id)
            )
        ).scalar_one()
    )

    by_flow: dict[uuid.UUID | None, list[OrderItem]] = {}
    for item in pending:
        by_flow.setdefault(item.kitchen_flow_id, []).append(item)

    now = utcnow()
    tickets: list[KitchenTicket] = []
    for flow_id, items in by_flow.items():
        sequence += 1
        ticket = KitchenTicket(
            order_id=order.id,
            kitchen_flow_id=flow_id,
            branch_id=order.branch_id,
            sequence=sequence,
            status=KitchenTicketStatusEnum.NEW.value,
            sent_at=now,
        )
        db.add(ticket)
        await db.flush()
        for item in items:
            db.add(
                KitchenTicketItem(
                    ticket_id=ticket.id,
                    order_item_id=item.id,
                    product_name=item.product_name,
                    quantity=item.quantity,
                    modifiers_summary=_summarise_options(item),
                    notes=item.kitchen_notes,
                    status=KitchenTicketStatusEnum.NEW.value,
                )
            )
            item.sent_to_kitchen_at = now
        tickets.append(ticket)

    await db.flush()

    # Re-load with items eagerly attached. The tickets were just constructed, so
    # their `items` collections are unloaded, and serialising them would trigger
    # a lazy load with no greenlet to run it on.
    ticket_ids = [t.id for t in tickets]
    refreshed = (
        (
            await db.execute(
                select(KitchenTicket)
                .where(KitchenTicket.id.in_(ticket_ids))
                .options(selectinload(KitchenTicket.items))
                .order_by(KitchenTicket.sequence)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    return list(refreshed)


def _summarise_options(item: OrderItem) -> str | None:
    """
    The one-line description of a line's choices, for a kitchen ticket.

    Quantities are spelled out. A mixed box reads "2 × Ferrero, Fudge"; naming
    each option once would have the kitchen pack one of each and be short by
    one brownie, with nothing on the ticket to show why.
    """
    parts: list[str] = []
    for option in item.selected_options_snapshot or []:
        name = option.get("name")
        if not name:
            continue
        quantity = int(option.get("quantity") or 1)
        parts.append(f"{quantity} × {name}" if quantity > 1 else str(name))
    return ", ".join(parts) if parts else None


# ─── Payment and close ────────────────────────────────────────────────────────


async def record_payment(
    db: AsyncSession,
    *,
    order: Order,
    user: User,
    payment_method_id: uuid.UUID,
    amount: Decimal,
    tendered: Decimal | None = None,
    tips: Decimal = Decimal("0"),
    till: Till | None = None,
    is_refund: bool = False,
    reference: str | None = None,
) -> OrderPayment:
    method = await db.get(PaymentMethod, payment_method_id)
    if method is None or method.deleted_at is not None or not method.is_active:
        raise BadRequestError("Payment method not available")

    amount = money(amount)
    if amount <= 0:
        raise BadRequestError("Payment amount must be positive")

    outstanding = order.balance_due
    if is_refund:
        net_paid = _net_paid(order)
        if amount > net_paid:
            raise ConflictError(
                f"Refund of {amount} exceeds the {net_paid} paid on this order"
            )
    elif amount > outstanding:
        raise ConflictError(
            f"Payment of {amount} exceeds the outstanding balance of {outstanding}"
        )

    tendered_amount = money(tendered if tendered is not None else amount)
    change = money(max(tendered_amount - amount, Decimal("0")))

    payment = OrderPayment(
        order_id=order.id,
        payment_method_id=method.id,
        till_id=till.id if till else order.till_id,
        user_id=user.id,
        amount=amount,
        tendered=tendered_amount,
        tips=money(tips),
        change_given=change,
        business_date=order.business_date or "",
        is_refund=is_refund,
        reference=reference,
        recorded_at=utcnow(),
    )
    db.add(payment)

    # Cash movements must hit the drawer ledger, or the till will not reconcile.
    # What stays in the drawer is `amount`: the customer hands over `tendered`
    # and takes `change` back, and tendered - change == amount by construction.
    # Recording amount - change would understate every over-tendered sale and
    # can even go negative.
    if method.type == PaymentMethodTypeEnum.CASH.value and till is not None:
        await till_service.add_drawer_operation(
            db,
            till=till,
            user=user,
            op_type=(
                DrawerOperationTypeEnum.RETURN.value
                if is_refund
                else DrawerOperationTypeEnum.SALES.value
            ),
            amount=amount,
            order_id=order.id,
            notes=f"Order {order.order_number}",
        )

    if tips:
        order.tips_amount = money(Decimal(str(order.tips_amount)) + money(tips))

    await db.flush()
    await db.refresh(payment)
    return payment


async def close_order(db: AsyncSession, *, order: Order, user: User) -> Order:
    """Close a fully-settled check and release its table."""
    order = await get_order(db, order.id)
    _assert_open(order)

    if order.balance_due > 0:
        raise ConflictError(f"Order still has {order.balance_due} outstanding")

    order.pos_status = PosOrderStatusEnum.CLOSED.value
    order.status = "confirmed"
    order.closer_id = user.id
    order.closed_at = utcnow()
    for item in order.items:
        if item.status == OrderItemStatusEnum.ACTIVE.value:
            item.status = OrderItemStatusEnum.CLOSED.value

    await _release_table(db, order)
    await db.flush()

    # Consume recipe ingredients. Deliberately after the order is marked closed
    # and deliberately swallowing failures: a stock problem must never strand a
    # paid check in an open state. Depletion is idempotent, so it can be retried.
    try:
        await inventory_service.deplete_for_order(db, order=order, user=user)
    except Exception:  # noqa: BLE001 — closing the sale outranks stock accuracy
        logger.exception("Inventory depletion failed for order %s", order.order_number)

    return await get_order(db, order.id)


async def void_order(
    db: AsyncSession, *, order: Order, user: User, reason_id: uuid.UUID | None = None
) -> Order:
    _assert_open(order)
    # What blocks a void is money still held against the order, not the fact
    # that money once moved. Testing for payment rows means an order that has
    # been fully refunded — net zero, nothing owed to anyone — can never be
    # voided, and the refund itself is a payment row, so following the error's
    # own instruction leaves the cashier exactly where they started.
    if _net_paid(order) != 0:
        raise ConflictError("This order has payments — refund them before voiding")

    order.pos_status = PosOrderStatusEnum.VOID.value
    order.status = "cancelled"
    order.voided_at = utcnow()
    order.void_reason_id = reason_id
    order.closer_id = user.id
    for item in order.items:
        item.status = OrderItemStatusEnum.VOID.value
        item.voided_by_id = user.id
        item.voided_at = utcnow()

    await _release_table(db, order)
    await db.flush()
    return await get_order(db, order.id)


async def split_order(
    db: AsyncSession,
    *,
    order: Order,
    user: User,
    item_ids: list[uuid.UUID],
) -> tuple[Order, Order]:
    """
    Move some lines onto a new check, for a table that wants to pay separately.

    The new check inherits the table, so both halves stay seated, and points
    back at the original through `original_order_id` so the pair can be traced
    after the fact. Order-level discounts and charges are deliberately not
    carried across: they were negotiated against a basket that no longer
    exists, and apportioning them silently would change what each guest owes.
    The cashier re-applies them to whichever check should bear them.
    """
    _assert_open(order)
    if order.payments:
        raise ConflictError("Take the split before any payment is recorded")

    wanted = set(item_ids)
    if not wanted:
        raise BadRequestError("Choose at least one item to move")

    moving = [i for i in order.items if i.id in wanted]
    if len(moving) != len(wanted):
        raise BadRequestError("Some of those items are not on this check")
    if len(moving) == len(order.items):
        raise BadRequestError("Move fewer items — a split cannot empty the check")

    branch = await db.get(Branch, order.branch_id)
    child = await open_order(
        db,
        branch=branch,
        user=user,
        order_type=order.order_type,
        till=await db.get(Till, order.till_id) if order.till_id else None,
        device_id=order.device_id,
        # Both checks stay on the table; it is one party, not two seatings.
        table_id=order.table_id,
        guests=1,
        customer_name=order.customer_name,
        customer_phone=order.customer_phone,
    )
    child.original_order_id = order.id

    for item in moving:
        item.order_id = child.id
    await db.flush()

    return await recalculate(db, await get_order(db, order.id)), await recalculate(
        db, await get_order(db, child.id)
    )


async def join_orders(
    db: AsyncSession, *, target: Order, source: Order, user: User
) -> Order:
    """
    Merge `source` into `target` — one table, one bill.

    The source is marked `joined` rather than deleted so the check number it
    burned stays accounted for; a gap in the sequence is the kind of thing an
    auditor asks about.
    """
    _assert_open(target)
    _assert_open(source)
    if target.id == source.id:
        raise BadRequestError("Cannot join a check to itself")
    if source.payments or target.payments:
        raise ConflictError("Join before either check takes a payment")
    if source.branch_id != target.branch_id:
        raise BadRequestError("Checks are at different branches")

    for item in list(source.items):
        item.order_id = target.id
    source.pos_status = PosOrderStatusEnum.JOINED.value
    source.status = "cancelled"
    source.original_order_id = target.id
    source.closer_id = user.id
    source.closed_at = utcnow()
    target.guests = (target.guests or 1) + (source.guests or 0)

    # Only free the table if the source was sitting somewhere else; the target
    # may well be on the same one.
    if source.table_id and source.table_id != target.table_id:
        await _release_table(db, source)

    await db.flush()
    await recalculate(db, await get_order(db, source.id))
    return await recalculate(db, await get_order(db, target.id))


async def change_table(
    db: AsyncSession, *, order: Order, table_id: uuid.UUID | None
) -> Order:
    """Move an open check to another table, or off the floor entirely."""
    _assert_open(order)
    if order.order_type != OrderTypeEnum.DINE_IN.value:
        raise BadRequestError("Only a dine-in check sits at a table")

    previous_id = order.table_id
    if table_id is not None:
        table = await db.get(PosTable, table_id)
        if table is None:
            raise BadRequestError("Table not found")
        if table.id != previous_id and table.status == TableStatusEnum.OCCUPIED.value:
            raise ConflictError(f"Table {table.name} already has an open check")
        table.status = TableStatusEnum.OCCUPIED.value

    order.table_id = table_id
    # Free the old table only after the new one is claimed, so a failure part
    # way through cannot leave the party with no table at all.
    if previous_id and previous_id != table_id:
        previous = await db.get(PosTable, previous_id)
        if previous is not None:
            previous.status = TableStatusEnum.FREE.value

    await db.flush()
    return await get_order(db, order.id)


async def park_order(db: AsyncSession, *, order: Order) -> Order:
    """
    Set a check aside without closing it.

    A queue forms, the customer is still deciding, and the till is needed for
    the person behind them. The table stays held — the party has not left —
    and the lines already fired stay fired, because the kitchen has started
    them.
    """
    _assert_open(order)
    if order.payments:
        raise ConflictError("Cannot park a check that already has a payment")
    order.pos_status = PosOrderStatusEnum.DRAFT.value
    await db.flush()
    return await get_order(db, order.id)


async def resume_order(db: AsyncSession, *, order: Order) -> Order:
    """Bring a parked check back to the register."""
    if order.pos_status != PosOrderStatusEnum.DRAFT.value:
        raise ConflictError("That check is not parked")
    order.pos_status = PosOrderStatusEnum.ACTIVE.value
    await db.flush()
    return await get_order(db, order.id)


async def _release_table(db: AsyncSession, order: Order) -> None:
    if order.table_id is None:
        return
    table = await db.get(PosTable, order.table_id)
    if table is not None:
        table.status = TableStatusEnum.FREE.value
