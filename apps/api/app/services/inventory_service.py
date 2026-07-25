"""
Inventory posting and costing.

Stock only ever moves by posting an `InventoryTransaction`. Posting is the one
place that touches `InventoryLevel`, which keeps the ledger authoritative and
makes levels rebuildable.

Costing is **weighted average**, recomputed on every receipt:

    new_average = (on_hand × average + received × unit_cost) / (on_hand + received)

Issues leave the average untouched and simply reduce quantity, so the value of
what remains is unchanged by a sale — which is what a moving-average system
should do.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.business_settings import BusinessSettings
from app.models.inventory import (
    TRANSACTION_SIGN,
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    ModifierOptionIngredient,
    ProductIngredient,
    PurchaseOrder,
    PurchaseOrderStatusEnum,
    TransactionStatusEnum,
    Warehouse,
)
from app.models.order import Order, OrderItem
from app.models.user import User
from app.services import business_day_service

__all__ = [
    "adjust_level",
    "default_warehouse",
    "deplete_for_order",
    "level_for",
    "next_reference",
    "post_transaction",
    "receive_purchase_order",
]

QUANTITY = Decimal("0.0001")
COST = Decimal("0.000001")


def _q(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(QUANTITY)


def _c(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(COST)


REFERENCE_PREFIX = {
    InventoryTransactionTypeEnum.PURCHASING.value: "PUR",
    InventoryTransactionTypeEnum.TRANSFER_SEND.value: "TRS",
    InventoryTransactionTypeEnum.TRANSFER_RECEIVE.value: "TRR",
    InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value: "ADJ",
    InventoryTransactionTypeEnum.RETURN_TO_SUPPLIER.value: "RTS",
    InventoryTransactionTypeEnum.PRODUCTION.value: "PRD",
    InventoryTransactionTypeEnum.CONSUMPTION_FROM_PRODUCTION.value: "CFP",
    InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value: "CFO",
    InventoryTransactionTypeEnum.RETURN_FROM_ORDERS.value: "RFO",
    InventoryTransactionTypeEnum.RETURN_FROM_TRANSFERS.value: "RFT",
    InventoryTransactionTypeEnum.WASTE_FROM_ORDERS.value: "WFO",
    InventoryTransactionTypeEnum.WASTE_FROM_PRODUCTION.value: "WFP",
    InventoryTransactionTypeEnum.COST_ADJUSTMENT.value: "CAD",
    InventoryTransactionTypeEnum.INVENTORY_COUNT.value: "CNT",
}


async def next_reference(db: AsyncSession, transaction_type: str) -> str:
    """Human-readable sequential reference, e.g. PUR-000123."""
    prefix = REFERENCE_PREFIX.get(transaction_type, "INV")
    count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(InventoryTransaction)
                .where(InventoryTransaction.type == transaction_type)
            )
        ).scalar_one()
    )
    return f"{prefix}-{count + 1:06d}"


async def default_warehouse(db: AsyncSession, branch_id: uuid.UUID) -> Warehouse:
    """
    The warehouse stock lands in for a branch, creating one on first use so a
    new branch never blocks a delivery.
    """
    stmt = (
        select(Warehouse)
        .where(Warehouse.branch_id == branch_id, Warehouse.deleted_at.is_(None))
        .order_by(Warehouse.is_default.desc(), Warehouse.created_at)
    )
    existing = (await db.execute(stmt)).scalars().first()
    if existing is not None:
        return existing

    branch = await db.get(Branch, branch_id)
    if branch is None:
        raise NotFoundError("Branch not found")
    warehouse = Warehouse(
        branch_id=branch_id, name=f"{branch.name} Store", is_default=True
    )
    db.add(warehouse)
    await db.flush()
    await db.refresh(warehouse)
    return warehouse


async def level_for(
    db: AsyncSession, item_id: uuid.UUID, warehouse_id: uuid.UUID
) -> InventoryLevel:
    stmt = select(InventoryLevel).where(
        InventoryLevel.item_id == item_id,
        InventoryLevel.warehouse_id == warehouse_id,
    )
    level = (await db.execute(stmt)).scalar_one_or_none()
    if level is None:
        level = InventoryLevel(
            item_id=item_id,
            warehouse_id=warehouse_id,
            quantity=Decimal("0"),
            average_cost=Decimal("0"),
        )
        db.add(level)
        await db.flush()
    return level


def apply_movement(
    level: InventoryLevel, quantity_delta: Decimal, unit_cost: Decimal | None
) -> None:
    """
    Move stock and maintain the weighted-average cost.

    Receipts blend the incoming cost into the average; issues leave it alone.
    A receipt onto a negative balance resets the average to the incoming cost
    rather than producing a nonsensical blend.
    """
    on_hand = _q(level.quantity)
    average = _c(level.average_cost)
    delta = _q(quantity_delta)

    if delta > 0 and unit_cost is not None:
        incoming_cost = _c(unit_cost)
        if on_hand <= 0:
            average = incoming_cost
        else:
            total_value = on_hand * average + delta * incoming_cost
            average = _c(total_value / (on_hand + delta))

    level.quantity = _q(on_hand + delta)
    level.average_cost = average


async def post_transaction(
    db: AsyncSession, *, transaction: InventoryTransaction, user: User
) -> InventoryTransaction:
    """
    Apply a draft/pending transaction to stock. Idempotent by refusal: an
    already-closed transaction cannot be posted twice.
    """
    if transaction.is_posted:
        raise ConflictError(f"{transaction.reference} has already been posted")
    if not transaction.items:
        raise BadRequestError("A transaction must have at least one line")

    sign = TRANSACTION_SIGN.get(transaction.type)
    if sign is None:
        raise BadRequestError(f"Unknown transaction type '{transaction.type}'")

    settings = (await db.execute(select(BusinessSettings).limit(1))).scalars().first()
    prevent_negative = bool(settings and settings.prevent_negative_stock)

    warehouse_id = (
        transaction.warehouse_id
        or (await default_warehouse(db, transaction.branch_id)).id
    )

    total = Decimal("0")
    for line in transaction.items:
        item = await db.get(InventoryItem, line.item_id)
        if item is None:
            raise BadRequestError(f"Inventory item {line.item_id} not found")

        # Normalise into the ingredient unit using the factor snapshotted on the
        # line, falling back to the item's current factor for new lines.
        factor = _c(line.conversion_factor or item.storage_to_ingredient_factor or 1)
        if line.unit == "ingredient":
            factor = Decimal("1")
        line.conversion_factor = factor
        normalised = _q(Decimal(str(line.quantity)) * factor)
        line.quantity_in_ingredient_unit = normalised

        line.total_cost = _q(
            Decimal(str(line.unit_cost or 0)) * Decimal(str(line.quantity))
        )
        total += line.total_cost

        # Adjustments and counts carry their own sign in the quantity.
        delta = normalised if sign >= 0 else -normalised
        if transaction.type in (
            InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value,
            InventoryTransactionTypeEnum.INVENTORY_COUNT.value,
        ):
            delta = normalised

        level = await level_for(db, line.item_id, warehouse_id)

        if transaction.type == InventoryTransactionTypeEnum.INVENTORY_COUNT.value:
            # A count sets the balance rather than moving it; the variance is
            # what the report cares about.
            line.expected_quantity = _q(level.quantity)
            delta = _q(normalised - _q(level.quantity))

        if prevent_negative and delta < 0 and _q(level.quantity) + delta < 0:
            raise ConflictError(
                f"{item.name}: only {_q(level.quantity)} {item.ingredient_unit} "
                f"available, cannot issue {abs(delta)}"
            )

        unit_cost_in_ingredient_unit = (
            _c(Decimal(str(line.unit_cost)) / factor) if factor else _c(line.unit_cost)
        )
        apply_movement(
            level, delta, unit_cost_in_ingredient_unit if delta > 0 else None
        )

        if transaction.type == InventoryTransactionTypeEnum.INVENTORY_COUNT.value:
            level.last_counted_at = utcnow()

    transaction.total_cost = _q(total + Decimal(str(transaction.additional_cost or 0)))
    transaction.warehouse_id = warehouse_id
    transaction.status = TransactionStatusEnum.CLOSED.value
    transaction.poster_id = user.id
    transaction.posted_at = utcnow()

    await db.flush()
    await db.refresh(transaction)
    return transaction


async def adjust_level(
    db: AsyncSession,
    *,
    branch: Branch,
    user: User,
    item_id: uuid.UUID,
    quantity_delta: Decimal,
    reason_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> InventoryTransaction:
    """Convenience wrapper for a single-line signed quantity adjustment."""
    business_date = await business_day_service.current_business_date(db, branch)
    item = await db.get(InventoryItem, item_id)
    if item is None:
        raise NotFoundError("Inventory item not found")

    transaction = InventoryTransaction(
        reference=await next_reference(
            db, InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value
        ),
        type=InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=branch.id,
        business_date=business_date,
        reason_id=reason_id,
        notes=notes,
        creator_id=user.id,
    )
    db.add(transaction)
    await db.flush()

    db.add(
        InventoryTransactionItem(
            transaction_id=transaction.id,
            item_id=item_id,
            quantity=_q(quantity_delta),
            unit="ingredient",
            conversion_factor=Decimal("1"),
            unit_cost=_c(item.cost),
        )
    )
    await db.flush()
    await db.refresh(transaction)
    return await post_transaction(db, transaction=transaction, user=user)


async def receive_purchase_order(
    db: AsyncSession,
    *,
    purchase_order: PurchaseOrder,
    user: User,
    received: dict[uuid.UUID, Decimal],
) -> InventoryTransaction:
    """
    Receive against an approved PO, in full or in part.

    `received` maps purchase-order-item id to the quantity actually delivered,
    so a short delivery leaves the PO partially received rather than closed.
    """
    if purchase_order.status not in (
        PurchaseOrderStatusEnum.APPROVED.value,
        PurchaseOrderStatusEnum.PARTIALLY_RECEIVED.value,
    ):
        raise ConflictError(
            f"Purchase order is {purchase_order.status}; only approved orders can be received"
        )

    branch = await db.get(Branch, purchase_order.branch_id)
    if branch is None:
        raise NotFoundError("Branch not found")
    business_date = await business_day_service.current_business_date(db, branch)

    transaction = InventoryTransaction(
        reference=await next_reference(
            db, InventoryTransactionTypeEnum.PURCHASING.value
        ),
        type=InventoryTransactionTypeEnum.PURCHASING.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=purchase_order.branch_id,
        warehouse_id=purchase_order.warehouse_id,
        supplier_id=purchase_order.supplier_id,
        purchase_order_id=purchase_order.id,
        business_date=business_date,
        creator_id=user.id,
    )
    db.add(transaction)
    await db.flush()

    any_line = False
    for po_item in purchase_order.items:
        quantity = _q(received.get(po_item.id, 0))
        if quantity <= 0:
            continue
        if quantity > po_item.outstanding_quantity:
            raise BadRequestError(
                f"Receiving {quantity} exceeds the {po_item.outstanding_quantity} outstanding"
            )
        any_line = True
        db.add(
            InventoryTransactionItem(
                transaction_id=transaction.id,
                item_id=po_item.item_id,
                quantity=quantity,
                unit=po_item.unit,
                conversion_factor=po_item.conversion_factor,
                unit_cost=po_item.unit_cost,
            )
        )
        po_item.received_quantity = _q(
            Decimal(str(po_item.received_quantity or 0)) + quantity
        )

    if not any_line:
        raise BadRequestError("Nothing was received")

    await db.flush()
    await db.refresh(transaction)
    posted = await post_transaction(db, transaction=transaction, user=user)

    purchase_order.status = (
        PurchaseOrderStatusEnum.CLOSED.value
        if purchase_order.is_fully_received
        else PurchaseOrderStatusEnum.PARTIALLY_RECEIVED.value
    )
    await db.flush()
    return posted


# ─── Depletion from sales ─────────────────────────────────────────────────────


async def deplete_for_order(
    db: AsyncSession, *, order: Order, user: User
) -> InventoryTransaction | None:
    """
    Consume ingredients for a closed order.

    Called once, when the check closes. Returns None when nothing on the order
    has a recipe, so a cafe that has not set up recipes yet is unaffected.
    """
    if order.branch_id is None:
        return None

    existing = (
        (
            await db.execute(
                select(InventoryTransaction).where(
                    InventoryTransaction.order_id == order.id,
                    InventoryTransaction.type
                    == InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        # Closing is idempotent; never double-deplete.
        return existing

    consumption = await _consumption_for_order(db, order)
    if not consumption:
        return None

    branch = await db.get(Branch, order.branch_id)
    if branch is None:
        return None

    transaction = InventoryTransaction(
        reference=await next_reference(
            db, InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value
        ),
        type=InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=order.branch_id,
        business_date=order.business_date
        or await business_day_service.current_business_date(db, branch),
        order_id=order.id,
        creator_id=user.id,
        notes=f"Auto-depletion for {order.order_number}",
    )
    db.add(transaction)
    await db.flush()

    for item_id, quantity in consumption.items():
        item = await db.get(InventoryItem, item_id)
        db.add(
            InventoryTransactionItem(
                transaction_id=transaction.id,
                item_id=item_id,
                quantity=_q(quantity),
                unit="ingredient",
                conversion_factor=Decimal("1"),
                unit_cost=_c(item.cost if item else 0),
            )
        )

    await db.flush()
    await db.refresh(transaction)
    # Depletion must never block closing a sale, so negative stock is tolerated
    # here even when the setting forbids it for manual transactions.
    try:
        return await post_transaction(db, transaction=transaction, user=user)
    except ConflictError:
        transaction.status = TransactionStatusEnum.PENDING.value
        transaction.notes = (
            f"{transaction.notes or ''} — held: would take stock negative"
        ).strip()
        await db.flush()
        return transaction


async def _consumption_for_order(
    db: AsyncSession, order: Order
) -> dict[uuid.UUID, Decimal]:
    """Total ingredient usage for an order, from product and modifier recipes."""
    totals: dict[uuid.UUID, Decimal] = {}

    stmt = select(OrderItem).where(OrderItem.order_id == order.id)
    items = list((await db.execute(stmt)).scalars().all())

    for line in items:
        if line.status == "void" or line.product_id is None:
            continue
        billable = max(line.quantity - (line.returned_quantity or 0), 0)
        if billable <= 0:
            continue

        recipes = list(
            (
                await db.execute(
                    select(ProductIngredient).where(
                        ProductIngredient.product_id == line.product_id
                    )
                )
            )
            .scalars()
            .all()
        )
        for recipe in recipes:
            inactive = recipe.inactive_in_order_types or []
            if order.order_type and order.order_type in inactive:
                continue
            totals[recipe.item_id] = _q(
                totals.get(recipe.item_id, Decimal("0"))
                + Decimal(str(recipe.quantity)) * billable
            )

        for option in line.selected_options_snapshot or []:
            raw_id = option.get("modifier_option_id")
            if not raw_id:
                continue
            try:
                option_id = uuid.UUID(str(raw_id))
            except (ValueError, AttributeError):
                continue
            option_quantity = int(option.get("quantity", 1) or 1)
            option_recipes = list(
                (
                    await db.execute(
                        select(ModifierOptionIngredient).where(
                            ModifierOptionIngredient.modifier_option_id == option_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            for recipe in option_recipes:
                totals[recipe.item_id] = _q(
                    totals.get(recipe.item_id, Decimal("0"))
                    + Decimal(str(recipe.quantity)) * billable * option_quantity
                )

    return {k: v for k, v in totals.items() if v > 0}


async def load_transaction(
    db: AsyncSession, transaction_id: uuid.UUID
) -> InventoryTransaction:
    stmt = (
        select(InventoryTransaction)
        .where(InventoryTransaction.id == transaction_id)
        .options(selectinload(InventoryTransaction.items))
    )
    transaction = (await db.execute(stmt)).scalars().unique().one_or_none()
    if transaction is None:
        raise NotFoundError("Inventory transaction not found")
    return transaction
