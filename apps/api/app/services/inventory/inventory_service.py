"""
Inventory posting and costing.

Stock only ever moves by posting an `InventoryTransaction`. Posting is the one
place that touches `InventoryLevel`, which keeps the ledger authoritative and
makes levels rebuildable.

Costing is **FIFO**. Each receipt lays down an `InventoryCostLayer`; each issue
consumes the oldest layers first (see `cost_layer_service`), recording the true
cost of the specific receipts it drew from. `InventoryLevel.average_cost` is then
*derived* — the weighted average of what still remains — so every reader keeps
working while cost of goods is first-in, first-out rather than a blend.
`apply_movement` below still maintains the level *quantity*; the valuation half
lives in the layers.

This module also owns the **purchase-order state machine** (below). It used to
live inline in `api/v1/inventory.py`, four endpoints each opening with its own
`if status != …` and then assigning the column by hand — while the order state
machine was a service (`order_lifecycle`) and the transfer state machine was
another (`transfer_service`). Three homes for one pattern is how they drift, and
`order_lifecycle` exists precisely because the order one already had: five sets
of rules, one of which quietly skipped the refund.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, event, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)

# Aliased to the existing private names: the implementation is shared,
# the call sites stay put, and `quantity` is already a local variable in
# both of these files.
from app.core.money import money as _money
from app.core.money import quantity as _q
from app.core.money import unit_cost as _c
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.business_settings import BusinessSettings
from app.models.inventory import (
    TRANSACTION_SIGN,
    CostLayerSourceKindEnum,
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderStatusEnum,
    Supplier,
    TransactionStatusEnum,
    Warehouse,
)
from app.models.inventory_v2 import (
    BranchInventorySettings,
    InventoryTrackingModeEnum,
)
from app.models.order import Order
from app.models.user import User
from app.services.inventory import cost_layer_service, supplier_service
from app.services.pos import business_day_service

__all__ = [
    "PURCHASE_ORDER_MOVES",
    "adjust_level",
    "allowed_purchase_order_transitions",
    "assert_can_transition_purchase_order",
    "can_transition_purchase_order",
    "default_warehouse",
    "deplete_for_order",
    "level_for",
    "inventory_item_cost_for_unit",
    "assert_warehouse_for_branch",
    "line_cost_in_storage_unit",
    "next_reference",
    "next_inventory_reference",
    "post_transaction",
    "build_po_lines",
    "create_pos_purchase_order",
    "rebuild_cost_layers",
    "receive_purchase_order",
    "transition_purchase_order",
]

logger = logging.getLogger(__name__)


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
    InventoryTransactionTypeEnum.WASTE_FROM_ORDERS.value: "WFO",
    InventoryTransactionTypeEnum.WASTE_FROM_PRODUCTION.value: "WFP",
    InventoryTransactionTypeEnum.COST_ADJUSTMENT.value: "CAD",
    InventoryTransactionTypeEnum.INVENTORY_COUNT.value: "CNT",
    InventoryTransactionTypeEnum.OPENING_BALANCE.value: "OPN",
    InventoryTransactionTypeEnum.INTERNAL_USE.value: "INT",
    InventoryTransactionTypeEnum.EXTRA_PRODUCTION_USE.value: "EPU",
}


async def assert_warehouse_for_branch(
    db: AsyncSession, warehouse_id: uuid.UUID, branch_id: uuid.UUID
) -> Warehouse:
    warehouse = await db.get(Warehouse, warehouse_id)
    if warehouse is None or warehouse.deleted_at is not None or not warehouse.is_active:
        raise NotFoundError("Stock container not found")
    if warehouse.branch_id != branch_id:
        raise ConflictError("Stock container does not belong to this branch")
    return warehouse


def inventory_item_cost_for_unit(item: InventoryItem, unit: str) -> Decimal:
    """The zero pre-cost fallback, in the requested unit.

    The item no longer carries a catalogue cost of its own — cost is FIFO, held in
    the item's cost layers and summarised on ``InventoryLevel.average_cost``. So an
    item with no costed stock yet is valued at 0 until its first receipt or
    production posts a real cost. Every caller here reaches this only *after*
    consulting the FIFO layer / level average for the warehouse in hand, so this is
    strictly the "no cost known" case. Kept as a function (rather than inlining a
    zero) so the ``storage``/``ingredient`` unit contract stays enforced.
    """
    if unit not in {"storage", "ingredient"}:
        raise BadRequestError(f"Unknown inventory entry unit '{unit}'")
    return _c(0)


def canonical_cost_for_unit(
    item: InventoryItem, storage_cost: Decimal, unit: str
) -> Decimal:
    """Convert a per-storage-unit moving average to an entered-unit cost.

    The ledger's canonical cost is per storage unit; a line entered in storage
    units carries it unchanged, one entered in ingredient units carries it
    divided by the factor (ingredient = storage ÷ factor for cost too).
    """
    if unit not in {"storage", "ingredient"}:
        raise BadRequestError(f"Unknown inventory entry unit '{unit}'")
    cost = Decimal(str(storage_cost))
    if unit == "storage":
        return _c(cost)
    factor = Decimal(str(item.storage_to_ingredient_factor or 1))
    if factor <= 0:
        raise BadRequestError(f"{item.name} has an invalid unit conversion factor")
    return _c(cost / factor)


def line_cost_in_storage_unit(line: InventoryTransactionItem) -> Decimal:
    """Read an immutable ledger line's cost as cost per storage unit.

    Storage is the canonical valuation unit; a storage-entered line's cost is
    already per storage, an ingredient-entered line's is per ingredient and
    multiplies by the snapshot factor to reach per storage.
    """
    if line.unit not in {"storage", "ingredient"}:
        raise BadRequestError(f"Unknown inventory entry unit '{line.unit}'")
    entered_cost = Decimal(str(line.unit_cost or 0))
    if line.unit == "storage":
        return _c(entered_cost)
    factor = Decimal(str(line.conversion_factor or 1))
    if factor <= 0:
        raise BadRequestError("A ledger line has an invalid conversion factor")
    return _c(entered_cost * factor)


async def next_reference(db: AsyncSession, transaction_type: str) -> str:
    """Human-readable sequential reference, e.g. PUR-000123."""
    prefix = REFERENCE_PREFIX.get(transaction_type, "INV")
    return await next_inventory_reference(db, prefix)


async def next_inventory_reference(db: AsyncSession, prefix: str) -> str:
    """Allocate a collision-free reference across concurrent branches."""
    value = int(
        (
            await db.execute(text("SELECT nextval('inventory_reference_sequence')"))
        ).scalar_one()
    )
    return f"{prefix}-{value:06d}"


async def default_warehouse(db: AsyncSession, branch_id: uuid.UUID) -> Warehouse:
    """
    The warehouse stock lands in for a branch, creating one on first use so a
    new branch never blocks a delivery.
    """
    branch = (
        await db.execute(select(Branch).where(Branch.id == branch_id).with_for_update())
    ).scalar_one_or_none()
    if branch is None:
        raise NotFoundError("Branch not found")

    stmt = (
        select(Warehouse)
        .where(
            Warehouse.branch_id == branch_id,
            Warehouse.deleted_at.is_(None),
            Warehouse.is_active.is_(True),
        )
        .order_by(Warehouse.is_default.desc(), Warehouse.created_at, Warehouse.id)
    )
    existing = (await db.execute(stmt)).scalars().first()
    if existing is not None:
        if not existing.is_default:
            existing.is_default = True
            await db.flush()
        return existing

    warehouse = Warehouse(
        branch_id=branch_id, name=f"{branch.name} Store", is_default=True
    )
    db.add(warehouse)
    await db.flush()
    await db.refresh(warehouse)
    return warehouse


async def level_for(
    db: AsyncSession,
    item_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> InventoryLevel:
    stmt = select(InventoryLevel).where(
        InventoryLevel.item_id == item_id,
        InventoryLevel.warehouse_id == warehouse_id,
    )
    if for_update:
        # Mutation path: hold the row so two concurrent postings cannot both
        # read the same balance and lose one movement.
        stmt = stmt.with_for_update()
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


def apply_reversal_movement(
    level: InventoryLevel, quantity_delta: Decimal, original_unit_cost: Decimal
) -> None:
    """Reverse a historical movement without leaving its value in stock.

    Positive reversals (undoing an issue) blend the original issued value back
    in. Negative reversals (undoing a receipt) remove that receipt's value from
    the current pool. If the correction drives stock non-positive there is no
    defensible denominator, so the last valid cost is retained and the negative
    validation balance remains visible.
    """
    on_hand = _q(level.quantity)
    average = _c(level.average_cost)
    delta = _q(quantity_delta)
    historical_cost = _c(original_unit_cost)
    if delta >= 0:
        apply_movement(level, delta, historical_cost)
        return

    new_quantity = _q(on_hand + delta)
    if on_hand > 0 and new_quantity > 0:
        remaining_value = on_hand * average + delta * historical_cost
        if remaining_value >= 0:
            average = _c(remaining_value / new_quantity)
    level.quantity = new_quantity
    level.average_cost = average


async def _forward_line_costing(
    db: AsyncSession,
    *,
    transaction: InventoryTransaction,
    line: InventoryTransactionItem,
    line_index: int,
    item: InventoryItem,
    warehouse_id: uuid.UUID,
    delta: Decimal,
    unit_cost_canonical: Decimal,
    on_hand_before: Decimal,
    posting_sequence: int,
    fallback_cost: Decimal,
) -> Decimal:
    """FIFO cost of one forward (non-reversal) movement line.

    Inbound receipts lay down a layer (after seeding a backfill layer for any
    pre-existing uncosted stock); positive counts/adjustments lay down a layer
    priced at the oldest surviving one; issues consume layers oldest-first.
    """
    if delta > 0:
        kind = cost_layer_service.receipt_layer_kind(transaction.type)
        if kind is not None:
            # An opening balance is often posted before a cost is known (the level
            # sits at 0). Value it at the item's catalogue cost rather than 0, so
            # a rebuild reproduces the same layer the cutover backfill seeded and
            # the two never disagree. A real receipt keeps its entered cost.
            if (
                kind is CostLayerSourceKindEnum.OPENING_BALANCE
                and unit_cost_canonical == 0
            ):
                unit_cost_canonical = inventory_item_cost_for_unit(item, "storage")
            await cost_layer_service.backfill_uncosted_stock(
                db,
                transaction=transaction,
                line=line,
                item=item,
                warehouse_id=warehouse_id,
                on_hand_before=on_hand_before,
                unit_cost=unit_cost_canonical,
                layer_index=line_index * 2,
            )
            # Receiving onto a negative balance (stock was issued before the
            # delivery was keyed) first cancels the outstanding shortfall: only
            # the quantity that survives above zero becomes a layer, so
            # Σ(remaining layers) keeps tracking the level's quantity instead of
            # laying a phantom layer over a still-negative balance.
            layer_qty = delta
            if on_hand_before < 0:
                layer_qty = max(Decimal("0"), _q(Decimal(str(on_hand_before)) + delta))
            if layer_qty > 0:
                await cost_layer_service.create_layer(
                    db,
                    transaction=transaction,
                    line=line,
                    item=item,
                    warehouse_id=warehouse_id,
                    quantity=layer_qty,
                    unit_cost=unit_cost_canonical,
                    source_kind=kind,
                    layer_index=line_index * 2 + 1,
                )
            return _money(delta * unit_cost_canonical)

        # A positive count/adjustment tops stock up at the earliest surviving
        # cost — the oldest layer's — falling back to the last average, then the
        # item's catalogue cost.
        price = await cost_layer_service.oldest_active_layer_cost(
            db, item.id, warehouse_id
        )
        if price is None:
            price = (
                fallback_cost
                if fallback_cost > 0
                else inventory_item_cost_for_unit(item, "storage")
            )
        overage_kind = (
            CostLayerSourceKindEnum.COUNT_OVERAGE
            if transaction.type == InventoryTransactionTypeEnum.INVENTORY_COUNT.value
            else CostLayerSourceKindEnum.POSITIVE_ADJUSTMENT
        )
        await cost_layer_service.create_layer(
            db,
            transaction=transaction,
            line=line,
            item=item,
            warehouse_id=warehouse_id,
            quantity=delta,
            unit_cost=price,
            source_kind=overage_kind,
            layer_index=line_index * 2 + 1,
        )
        return _money(delta * price)

    if delta < 0:
        return await cost_layer_service.consume_fifo(
            db,
            line=line,
            item=item,
            warehouse_id=warehouse_id,
            quantity=-delta,
            posting_sequence=posting_sequence,
            fallback_cost=fallback_cost,
        )
    return Decimal("0.00")


async def _reverse_line_costing(
    db: AsyncSession,
    *,
    transaction: InventoryTransaction,
    line: InventoryTransactionItem,
    item: InventoryItem,
    warehouse_id: uuid.UUID,
    delta: Decimal,
    unit_cost_canonical: Decimal,
    posting_sequence: int,
    fallback_cost: Decimal,
) -> Decimal:
    """FIFO cost of one reversal line.

    Undoing an issue restores the exact layers it drew from; undoing a receipt
    removes stock from the layers it created.

    A reversal predating this feature has no ``reverses_line_id``, so the exact
    layers cannot be found. Rather than touch nothing (which would let a rebuild's
    Σ remaining drift from the running quantity), treat it as a plain movement in
    the reversal's own direction: a positive reversal lays a fresh layer at the
    prior cost, a negative one consumes FIFO. Quantity and valuation stay
    consistent even though the original provenance is lost.
    """
    if line.reverses_line_id is None:
        if delta > 0:
            price = fallback_cost if fallback_cost > 0 else _c(0)
            await cost_layer_service.create_layer(
                db,
                transaction=transaction,
                line=line,
                item=item,
                warehouse_id=warehouse_id,
                quantity=delta,
                unit_cost=price,
                source_kind=CostLayerSourceKindEnum.POSITIVE_ADJUSTMENT,
                layer_index=0,
            )
            return _money(delta * price)
        if delta < 0:
            return await cost_layer_service.consume_fifo(
                db,
                line=line,
                item=item,
                warehouse_id=warehouse_id,
                quantity=-delta,
                posting_sequence=posting_sequence,
                fallback_cost=fallback_cost,
            )
        return Decimal("0.00")
    if delta > 0:
        return await cost_layer_service.restore_consumed_line(
            db,
            original_line_id=line.reverses_line_id,
            item=item,
            warehouse_id=warehouse_id,
        )
    if delta < 0:
        return await cost_layer_service.reverse_receipt_line(
            db,
            original_line_id=line.reverses_line_id,
            reversal_line=line,
            item=item,
            warehouse_id=warehouse_id,
            quantity=-delta,
            posting_sequence=posting_sequence,
            fallback_cost=fallback_cost,
        )
    return Decimal("0.00")


async def rebuild_cost_layers(
    db: AsyncSession, *, branch_id: uuid.UUID, warehouse_id: uuid.UUID
) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    """
    Regenerate every FIFO layer and consumption for a warehouse from the ledger.

    The layers are a projection: replaying the immutable closed lines in
    ``posting_sequence`` order through the *same* costing primitives the live
    posting path uses guarantees the rebuild and the engine can never diverge.
    Returns each item's rebuilt ``(quantity, average_cost)`` so the caller can
    detect drift and restate levels. Deletes and rewrites layers, so callers
    that only want a preview run it inside a savepoint they roll back.
    """
    await cost_layer_service.delete_branch_layers(db, branch_id, warehouse_id)
    rows = (
        await db.execute(
            select(InventoryTransaction, InventoryTransactionItem)
            .join(
                InventoryTransactionItem,
                InventoryTransactionItem.transaction_id == InventoryTransaction.id,
            )
            .where(
                InventoryTransaction.branch_id == branch_id,
                InventoryTransaction.warehouse_id == warehouse_id,
                InventoryTransaction.status == TransactionStatusEnum.CLOSED.value,
            )
            .order_by(
                InventoryTransaction.posting_sequence,
                InventoryTransactionItem.id,
            )
        )
    ).all()

    running_qty: dict[uuid.UUID, Decimal] = {}
    line_index: dict[uuid.UUID, int] = {}
    items: dict[uuid.UUID, InventoryItem] = {}
    for transaction, line in rows:
        item = items.get(line.item_id)
        if item is None:
            item = await db.get(InventoryItem, line.item_id)
            items[line.item_id] = item
        delta = _q(line.signed_quantity or 0)
        on_hand_before = running_qty.get(line.item_id, Decimal("0"))
        unit_cost_canonical = line_cost_in_storage_unit(line)
        fallback = _c(line.previous_unit_cost or 0)
        if transaction.type == InventoryTransactionTypeEnum.COST_ADJUSTMENT.value:
            await cost_layer_service.rescale_layers_to_average(
                db,
                item=item,
                warehouse_id=warehouse_id,
                new_average=unit_cost_canonical,
            )
        elif transaction.reverses_transaction_id is not None:
            await _reverse_line_costing(
                db,
                transaction=transaction,
                line=line,
                item=item,
                warehouse_id=warehouse_id,
                delta=delta,
                unit_cost_canonical=unit_cost_canonical,
                posting_sequence=transaction.posting_sequence,
                fallback_cost=fallback,
            )
        else:
            idx = line_index.get(line.item_id, 0)
            line_index[line.item_id] = idx + 1
            await _forward_line_costing(
                db,
                transaction=transaction,
                line=line,
                line_index=idx,
                item=item,
                warehouse_id=warehouse_id,
                delta=delta,
                unit_cost_canonical=unit_cost_canonical,
                on_hand_before=on_hand_before,
                posting_sequence=transaction.posting_sequence,
                fallback_cost=fallback,
            )
        running_qty[line.item_id] = _q(on_hand_before + delta)

    ledger: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    for item_id, item in items.items():
        average = await cost_layer_service.derive_average_cost(db, item, warehouse_id)
        ledger[item_id] = (running_qty.get(item_id, Decimal("0")), average)
    return ledger


async def post_transaction(
    db: AsyncSession, *, transaction: InventoryTransaction, user: User | None
) -> InventoryTransaction:
    """
    Apply a draft/pending transaction to stock. Idempotent by refusal: an
    already-closed transaction cannot be posted twice.
    """
    if transaction.is_posted:
        raise ConflictError(f"{transaction.reference} has already been posted")
    if not transaction.items:
        raise BadRequestError("A transaction must have at least one line")

    # Every writer—not only order consumption—serializes on the branch before
    # it receives a posting sequence or locks level rows. PostgreSQL advisory
    # transaction locks are re-entrant, so callers that already hold this lock
    # (the source-event worker and count approval) safely pass through.
    from app.services.inventory import source_event_service

    await source_event_service.lock_branch_inventory(db, transaction.branch_id)

    sign = TRANSACTION_SIGN.get(transaction.type)
    if sign is None:
        raise BadRequestError(f"Unknown transaction type '{transaction.type}'")

    settings = (await db.execute(select(BusinessSettings).limit(1))).scalars().first()
    branch_settings = (
        (
            await db.execute(
                select(BranchInventorySettings).where(
                    BranchInventorySettings.branch_id == transaction.branch_id
                )
            )
        )
        .scalars()
        .one_or_none()
    )
    bootstrap_types = {
        InventoryTransactionTypeEnum.OPENING_BALANCE.value,
        InventoryTransactionTypeEnum.INVENTORY_COUNT.value,
    }
    if (
        branch_settings is None or not branch_settings.inventory_enabled
    ) and transaction.type not in bootstrap_types:
        raise ConflictError("Inventory movements are not enabled for this branch")
    prevent_negative = bool(settings and settings.prevent_negative_stock)
    if branch_settings and (
        branch_settings.validation_mode or branch_settings.allow_negative_stock
    ):
        prevent_negative = False

    if transaction.posting_sequence is None:
        transaction.posting_sequence = int(
            (
                await db.execute(text("SELECT nextval('inventory_posting_sequence')"))
            ).scalar_one()
        )
    posting_sequence = transaction.posting_sequence

    if transaction.warehouse_id is not None:
        warehouse_id = (
            await assert_warehouse_for_branch(
                db, transaction.warehouse_id, transaction.branch_id
            )
        ).id
    else:
        warehouse_id = (await default_warehouse(db, transaction.branch_id)).id
    if transaction.other_warehouse_id is not None:
        if transaction.other_branch_id is None:
            raise BadRequestError(
                "A destination branch is required for its stock container"
            )
        await assert_warehouse_for_branch(
            db, transaction.other_warehouse_id, transaction.other_branch_id
        )

    total = Decimal("0")
    for line_index, line in enumerate(transaction.items):
        item = await db.get(InventoryItem, line.item_id)
        if item is None:
            raise BadRequestError(f"Inventory item {line.item_id} not found")
        if item.tracking_mode == InventoryTrackingModeEnum.PHANTOM.value:
            raise BadRequestError(
                f"{item.name} is a phantom recipe item and cannot hold stock"
            )
        if line.unit not in {"storage", "ingredient"}:
            raise BadRequestError(f"Unknown inventory entry unit '{line.unit}'")

        # Normalise into the canonical STORAGE unit using the factor snapshotted
        # on the line, falling back to the item's current factor for new lines.
        # factor = ingredient units per one storage unit (ingredient = storage ×
        # factor), so a storage-entered line passes straight through and an
        # ingredient-entered line (a recipe consumption) divides by the factor to
        # reach the grams actually taken off the shelf. Both figures are recorded
        # so a movement shows the amount in each unit.
        factor = _c(line.conversion_factor or item.storage_to_ingredient_factor or 1)
        if factor <= 0:
            raise BadRequestError(f"{item.name} has an invalid unit conversion factor")
        line.conversion_factor = factor
        entered = Decimal(str(line.quantity))
        if line.unit == "ingredient":
            storage_quantity = _q(entered / factor)
            ingredient_quantity = _q(entered)
        else:
            storage_quantity = _q(entered)
            ingredient_quantity = _q(entered * factor)
        line.quantity_in_storage_unit = storage_quantity
        line.quantity_in_ingredient_unit = ingredient_quantity
        normalised = storage_quantity

        # Adjustments and counts carry their own sign in the quantity.
        delta = normalised if sign >= 0 else -normalised
        if transaction.type in (
            InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value,
            InventoryTransactionTypeEnum.INVENTORY_COUNT.value,
            InventoryTransactionTypeEnum.OPENING_BALANCE.value,
        ):
            delta = normalised

        level = await level_for(db, line.item_id, warehouse_id, for_update=True)

        if transaction.type in (
            InventoryTransactionTypeEnum.INVENTORY_COUNT.value,
            InventoryTransactionTypeEnum.OPENING_BALANCE.value,
        ):
            # A count and an opening balance SET the balance to the counted figure
            # rather than adding it. Posting an opening balance as an addition let a
            # second opening count (or a re-run) double the stock; setting it means
            # the balance lands on the count whatever was there before. The variance
            # is what the report cares about.
            line.expected_quantity = _q(level.quantity)
            delta = _q(normalised - _q(level.quantity))

        unit_cost_canonical = line_cost_in_storage_unit(line)
        line.previous_unit_cost = _c(level.average_cost)
        if transaction.type == InventoryTransactionTypeEnum.COST_ADJUSTMENT.value:
            # Restate what stock is worth without moving any of it: rescale the
            # surviving FIFO layers so their weighted average becomes the entered
            # cost, then derive the level's average back from them.
            line.quantity = _q(level.quantity)
            line.quantity_in_storage_unit = _q(level.quantity)
            line.quantity_in_ingredient_unit = _q(Decimal(str(level.quantity)) * factor)
            line.signed_quantity = Decimal("0")
            value_change = await cost_layer_service.rescale_layers_to_average(
                db,
                item=item,
                warehouse_id=warehouse_id,
                new_average=unit_cost_canonical,
            )
            await cost_layer_service.refresh_level_average(db, level, item)
            # With no surviving layers there is nothing to rescale, so the entered
            # cost would otherwise be lost. Record it on the level directly so the
            # revaluation is not silently discarded (a receipt onto empty resets
            # the average from its own layer, so this only holds until then).
            layer_qty, _ = await cost_layer_service.remaining_totals(
                db, item.id, warehouse_id
            )
            if layer_qty <= 0:
                level.average_cost = unit_cost_canonical
            line.total_cost = value_change
            line.balance_after_quantity = _q(level.quantity)
            line.balance_after_value = _money(
                Decimal(str(level.quantity)) * Decimal(str(level.average_cost))
            )
            level.projected_through_sequence = posting_sequence
            total += Decimal(str(line.total_cost))
            continue

        if prevent_negative and delta < 0 and _q(level.quantity) + delta < 0:
            raise ConflictError(
                f"{item.name}: only {_q(level.quantity)} {item.storage_unit} "
                f"available, cannot issue {abs(delta)}"
            )

        # Quantity is maintained by apply_movement; valuation is FIFO and lives
        # in the cost layers, so the average is *derived* from what remains
        # rather than blended here.
        on_hand_before = _q(level.quantity)
        if transaction.reverses_transaction_id is not None:
            apply_reversal_movement(level, delta, unit_cost_canonical)
            line.total_cost = await _reverse_line_costing(
                db,
                transaction=transaction,
                line=line,
                item=item,
                warehouse_id=warehouse_id,
                delta=delta,
                unit_cost_canonical=unit_cost_canonical,
                posting_sequence=posting_sequence,
                fallback_cost=_c(line.previous_unit_cost or 0),
            )
        else:
            apply_movement(level, delta, None)
            line.total_cost = await _forward_line_costing(
                db,
                transaction=transaction,
                line=line,
                line_index=line_index,
                item=item,
                warehouse_id=warehouse_id,
                delta=delta,
                unit_cost_canonical=unit_cost_canonical,
                on_hand_before=on_hand_before,
                posting_sequence=posting_sequence,
                fallback_cost=_c(line.previous_unit_cost or 0),
            )

        await cost_layer_service.refresh_level_average(db, level, item)
        line.signed_quantity = _q(delta)
        total += Decimal(str(line.total_cost))
        line.balance_after_quantity = _q(level.quantity)
        line.balance_after_value = _money(
            Decimal(str(level.quantity)) * Decimal(str(level.average_cost))
        )
        level.projected_through_sequence = posting_sequence

        if transaction.type == InventoryTransactionTypeEnum.INVENTORY_COUNT.value:
            level.last_counted_at = utcnow()

    transaction.total_cost = _money(
        total + Decimal(str(transaction.additional_cost or 0))
    )
    transaction.warehouse_id = warehouse_id
    transaction.status = TransactionStatusEnum.CLOSED.value
    transaction.poster_id = user.id if user else None
    transaction.posted_at = utcnow()
    transaction.occurred_at = transaction.occurred_at or transaction.created_at

    # The database trigger rejects ad-hoc draft→closed updates. This
    # transaction-local marker says the projection and immutable snapshots were
    # prepared by this one posting path before the lifecycle changed.
    await db.execute(text("SET LOCAL mm.inventory_posting = 'on'"))
    await db.flush()
    await db.refresh(transaction)
    await db.execute(text("SET LOCAL mm.inventory_posting = 'off'"))
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
    warehouse_id: uuid.UUID | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    correction_group_id: uuid.UUID | None = None,
) -> InventoryTransaction:
    """Convenience wrapper for a single-line signed quantity adjustment.

    ``source_type``/``source_id``/``correction_group_id`` let a caller attribute
    the adjustment to what caused it and group several under one id — the
    transfer-order override posts one top-up per short item, all sharing the
    order's ``adjustment_group_id`` so the mini stock-adjustment report can read
    them back as a group. Left null, this is a plain manual adjustment as before.
    ``warehouse_id`` pins the location; the poster resolves the default when null.
    """
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
        warehouse_id=warehouse_id,
        business_date=business_date,
        reason_id=reason_id,
        notes=notes,
        creator_id=user.id,
        source_type=source_type,
        source_id=source_id,
        correction_group_id=correction_group_id,
    )
    db.add(transaction)
    await db.flush()

    db.add(
        InventoryTransactionItem(
            transaction_id=transaction.id,
            item_id=item_id,
            quantity=_q(quantity_delta),
            unit="storage",
            conversion_factor=Decimal(str(item.storage_to_ingredient_factor or 1)),
            unit_cost=inventory_item_cost_for_unit(item, "storage"),
        )
    )
    await db.flush()
    await db.refresh(transaction)
    return await post_transaction(db, transaction=transaction, user=user)


# ─── Purchase-order lifecycle ─────────────────────────────────────────────────
#
# The state machine used to be spread across four endpoints in
# `api/v1/inventory.py`: each opened with its own `if purchase_order.status !=
# …: raise ConflictError(…)` and then assigned the column and stamped an actor
# by hand. Nothing named the shape of the machine, so the only way to answer
# "what can a declined order do next?" was to read four handlers and hope there
# was no fifth writer. There nearly was — `receive_purchase_order` here already
# was one, using a different guard and a different message.
#
# Same treatment as `order_lifecycle`: one map, one function that validates,
# assigns and carries the consequences. The router keeps auth and schema
# mapping and stops keeping rules.


@dataclass(frozen=True)
class _Move:
    """
    One arrival state, and what it takes to get there.

    `refusal` is a template rather than a generated sentence because these
    messages predate the extraction and reach a person in the console. A
    refactor that promises identical behaviour includes identical words, so
    each is the endpoint's own wording, moved rather than rewritten.
    """

    sources: frozenset[PurchaseOrderStatusEnum]
    refusal: str
    #: Whoever raised the order may not be the one to wave it through.
    separation_of_duties: bool = False


#: The whole machine. Statuses not named here are terminal.
#:
#: `declined` is an ending on purpose: there is no route back to `draft`,
#: because the shop's answer to a rejected order is a new one rather than a
#: quietly re-edited copy of the one somebody already refused. `closed` is the
#: other ending — stock has moved and the books have it.
PURCHASE_ORDER_MOVES: dict[PurchaseOrderStatusEnum, _Move] = {
    PurchaseOrderStatusEnum.PENDING: _Move(
        sources=frozenset({PurchaseOrderStatusEnum.DRAFT}),
        refusal="Only draft orders can be submitted (this is {status})",
    ),
    PurchaseOrderStatusEnum.APPROVED: _Move(
        sources=frozenset({PurchaseOrderStatusEnum.PENDING}),
        refusal="Only submitted orders can be approved",
        separation_of_duties=True,
    ),
    PurchaseOrderStatusEnum.DECLINED: _Move(
        sources=frozenset({PurchaseOrderStatusEnum.PENDING}),
        refusal="Only submitted orders can be declined",
    ),
    # Receiving is now one-shot: `receive_purchase_order` always closes the order
    # (whatever was short is recorded on the line), so PARTIALLY_RECEIVED is no
    # longer reached from a receive. The transition is kept so an order left
    # `partially_received` by the old multi-delivery flow can still be received to
    # CLOSED, and its status stays a legal move source below.
    PurchaseOrderStatusEnum.PARTIALLY_RECEIVED: _Move(
        sources=frozenset(
            {
                PurchaseOrderStatusEnum.APPROVED,
                PurchaseOrderStatusEnum.PARTIALLY_RECEIVED,
            }
        ),
        refusal="Purchase order is {status}; only approved orders can be received",
    ),
    PurchaseOrderStatusEnum.CLOSED: _Move(
        sources=frozenset(
            {
                PurchaseOrderStatusEnum.APPROVED,
                PurchaseOrderStatusEnum.PARTIALLY_RECEIVED,
            }
        ),
        refusal="Purchase order is {status}; only approved orders can be received",
    ),
    # Cancel a PO after the fact. Reachable from every non-terminal state:
    # voiding a received order (closed/partially_received) reverses its stock and
    # restates the weighted-average cost; voiding an un-received one (draft/
    # pending/approved) just cancels the paperwork. `declined` and `voided`
    # themselves are endings and cannot be voided again.
    PurchaseOrderStatusEnum.VOIDED: _Move(
        sources=frozenset(
            {
                PurchaseOrderStatusEnum.DRAFT,
                PurchaseOrderStatusEnum.PENDING,
                PurchaseOrderStatusEnum.APPROVED,
                PurchaseOrderStatusEnum.PARTIALLY_RECEIVED,
                PurchaseOrderStatusEnum.CLOSED,
            }
        ),
        refusal="Purchase order is {status} and cannot be voided",
    ),
}


def _po_status(value) -> PurchaseOrderStatusEnum | None:
    """The enum for a column that stores its value as a string."""
    if isinstance(value, PurchaseOrderStatusEnum):
        return value
    try:
        return PurchaseOrderStatusEnum(value)
    except ValueError:
        return None


def allowed_purchase_order_transitions(
    current: PurchaseOrderStatusEnum | str,
) -> set[PurchaseOrderStatusEnum]:
    """Everywhere a purchase order in *current* may go. Empty means terminal."""
    status = _po_status(current)
    return {
        target
        for target, move in PURCHASE_ORDER_MOVES.items()
        if status in move.sources
    }


def can_transition_purchase_order(
    current: PurchaseOrderStatusEnum | str, new: PurchaseOrderStatusEnum
) -> bool:
    """Whether the map allows moving from *current* to *new*."""
    move = PURCHASE_ORDER_MOVES.get(new)
    return move is not None and _po_status(current) in move.sources


def assert_can_transition_purchase_order(
    purchase_order: PurchaseOrder,
    new_status: PurchaseOrderStatusEnum,
    *,
    user: User | None = None,
) -> None:
    """
    Raise unless this move is legal for this order and this person.

    Public because `receive_purchase_order` has to ask *before* it does the
    work: the status it ends on is computed from what arrived, so the guard
    cannot wait for the assignment. Everything else gets this for free by
    calling `transition_purchase_order`.
    """
    move = PURCHASE_ORDER_MOVES.get(new_status)
    if move is None:
        raise ConflictError(f"'{new_status.value}' is not a state an order moves to")

    if _po_status(purchase_order.status) not in move.sources:
        raise ConflictError(move.refusal.format(status=purchase_order.status))

    # Separation of duties: whoever raised the order cannot approve their own.
    # A rule about the transition rather than about the endpoint — which is
    # what it was, and why it only ever ran on one of the ways in.
    if (
        move.separation_of_duties
        and user is not None
        and purchase_order.submitter_id == user.id
        and not user.is_admin
    ):
        raise ForbiddenError("A purchase order must be approved by someone else")


#: Set while `transition_purchase_order` is the one assigning the column. A
#: plain flag rather than the ContextVar `order_lifecycle` uses: purchase-order
#: transitions are console actions on a single row with no await between the
#: set and the reset, so there is no interleaving for two requests to confuse.
_PO_GATED = False


@event.listens_for(PurchaseOrder.status, "set", active_history=True)
def _warn_on_ungated_po_write(target: PurchaseOrder, value, oldvalue, _initiator):
    """
    A status write that did not come through `transition_purchase_order`.

    The twin of `order_lifecycle._warn_on_ungated_write`, and for the same
    reason: the machine was spread across four handlers once and the only thing
    stopping it happening again is noticing. A warning rather than a raise —
    the writers this extraction knows about all go through the gate, and the
    ones it does not know about are the ones a raise would turn from a logged
    inconsistency into a broken request. Creation writes (no previous value)
    stay free: building an order in a state is not a transition.
    """
    if _PO_GATED:
        return
    old = getattr(oldvalue, "value", oldvalue)
    new = getattr(value, "value", value)
    if not isinstance(old, str) or not isinstance(new, str) or old == new:
        return
    logger.warning(
        "PurchaseOrder.status written outside inventory_service."
        "transition_purchase_order(): %s %s -> %s. The write stands, but its "
        "validation and consequences were skipped.",
        getattr(target, "reference", target.id),
        old,
        new,
    )


async def transition_purchase_order(
    db: AsyncSession,
    purchase_order: PurchaseOrder,
    new_status: PurchaseOrderStatusEnum,
    *,
    user: User,
) -> bool:
    """
    Move a purchase order to *new_status*, with everything that move implies.

    Returns whether anything moved. `False` means it was already there — which
    is the ordinary answer for a second partial receipt, and not an error.

    No flush and no commit beyond what the consequences need: services flush,
    the request commits.
    """
    global _PO_GATED

    if purchase_order.id is not None:
        purchase_order = await _lock_purchase_order(db, purchase_order.id)

    if _po_status(purchase_order.status) == new_status:
        return False

    assert_can_transition_purchase_order(purchase_order, new_status, user=user)

    _PO_GATED = True
    try:
        purchase_order.status = new_status.value
    finally:
        _PO_GATED = False

    _purchase_order_consequences(purchase_order, new_status, user)
    return True


async def _lock_purchase_order(
    db: AsyncSession, purchase_order_id: uuid.UUID
) -> PurchaseOrder:
    purchase_order = (
        (
            await db.execute(
                select(PurchaseOrder)
                .where(PurchaseOrder.id == purchase_order_id)
                .options(selectinload(PurchaseOrder.items))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .unique()
        .one_or_none()
    )
    if purchase_order is None:
        raise NotFoundError("Purchase order not found")
    return purchase_order


def _purchase_order_consequences(
    purchase_order: PurchaseOrder, new_status: PurchaseOrderStatusEnum, user: User
) -> None:
    """
    What arriving at a status makes happen, whoever brought the order there.

    Only the audit stamps, for now — the one consequence with real weight
    (stock arriving) belongs to `receive_purchase_order`, because it is
    computed from the delivery rather than implied by the status. Keyed off the
    transition all the same, so a second way to submit or approve an order
    cannot ship without the trail.

    Note the asymmetry in the declined case: `approver_id` is stamped and no
    timestamp is, because `purchase_orders` has `approved_at` and no
    `declined_at`. Preserved rather than fixed here — inventing a column is a
    migration, and this extraction promises identical behaviour.
    """
    if new_status == PurchaseOrderStatusEnum.PENDING:
        purchase_order.submitter_id = user.id
        purchase_order.submitted_at = utcnow()
    elif new_status == PurchaseOrderStatusEnum.APPROVED:
        purchase_order.approver_id = user.id
        purchase_order.approved_at = utcnow()
    elif new_status == PurchaseOrderStatusEnum.DECLINED:
        purchase_order.approver_id = user.id
    elif new_status == PurchaseOrderStatusEnum.VOIDED:
        purchase_order.voided_by = user.id
        purchase_order.voided_at = utcnow()


async def build_po_lines(
    db: AsyncSession,
    purchase_order: PurchaseOrder,
    lines,
    *,
    is_vat_deductible: bool,
) -> None:
    """Replace a PO's lines from input, splitting VAT and freezing its totals.

    Each input line carries the quantity and the VAT-inclusive line total; the
    per-unit cost (gross) and the recoverable VAT slice are derived here so both
    the admin create/edit and the till's create-and-receive price identically.
    """
    await db.execute(
        delete(PurchaseOrderItem).where(
            PurchaseOrderItem.purchase_order_id == purchase_order.id
        )
    )
    subtotal_net = Decimal("0")
    vat_total = Decimal("0")
    gross_total = Decimal("0")
    for line in lines:
        item = await db.get(InventoryItem, line.item_id)
        if item is None:
            raise BadRequestError(f"Inventory item {line.item_id} not found")
        split = supplier_service.split_line_vat(
            line.entered_total, line.quantity, is_vat_deductible=is_vat_deductible
        )
        subtotal_net += split.net_total
        vat_total += split.vat_amount
        gross_total += split.total
        db.add(
            PurchaseOrderItem(
                purchase_order_id=purchase_order.id,
                item_id=line.item_id,
                quantity=line.quantity,
                unit=line.unit,
                conversion_factor=(
                    Decimal("1")
                    if line.unit == "ingredient"
                    else Decimal(str(item.storage_to_ingredient_factor))
                ),
                entered_total=split.total,
                vat_amount=split.vat_amount,
                net_total=split.net_total,
                unit_cost=split.unit_cost,
                total_cost=split.total,
            )
        )
    additional = Decimal(str(purchase_order.additional_cost or 0))
    purchase_order.subtotal_net = _money(subtotal_net)
    purchase_order.vat_total = _money(vat_total)
    purchase_order.total_gross = _money(gross_total)
    purchase_order.total_cost = _money(gross_total + additional)
    await db.flush()


async def create_pos_purchase_order(
    db: AsyncSession,
    *,
    branch: Branch,
    user: User,
    supplier: Supplier,
    warehouse_id: uuid.UUID | None,
    data,
    invoice_object_key: str | None = None,
    invoice_content_type: str | None = None,
) -> tuple[PurchaseOrder, InventoryTransaction]:
    """Create a purchase order at the till and receive it in one action.

    The order is born approved (its receipt is the approval — there is no
    separate maker/checker at the counter) and immediately received in full, so
    stock and its FIFO cost land the moment the delivery is keyed in.
    """
    business_date = await business_day_service.current_business_date(db, branch)
    purchase_order = PurchaseOrder(
        reference=await next_inventory_reference(db, "PO"),
        status=PurchaseOrderStatusEnum.APPROVED.value,
        origin="pos",
        supplier_id=supplier.id,
        branch_id=branch.id,
        warehouse_id=warehouse_id,
        business_date=business_date,
        # A till PO is received the moment it is keyed, so the delivery is today
        # (the branch business date) — never a date the cashier picks.
        delivery_date=date.fromisoformat(business_date),
        supplier_reference=data.supplier_reference,
        invoice_object_key=invoice_object_key,
        invoice_content_type=invoice_content_type,
        notes=data.notes,
        creator_id=user.id,
        submitter_id=user.id,
        approver_id=user.id,
        submitted_at=utcnow(),
        approved_at=utcnow(),
    )
    db.add(purchase_order)
    await db.flush()
    await build_po_lines(
        db, purchase_order, data.items, is_vat_deductible=supplier.is_vat_deductible
    )
    purchase_order = await _lock_purchase_order(db, purchase_order.id)
    received = {line.id: line.quantity for line in purchase_order.items}
    transaction = await receive_purchase_order(
        db, purchase_order=purchase_order, user=user, received=received
    )
    return purchase_order, transaction


async def receive_purchase_order(
    db: AsyncSession,
    *,
    purchase_order: PurchaseOrder,
    user: User,
    received: dict[uuid.UUID, Decimal],
    reasons: dict[uuid.UUID, str] | None = None,
) -> InventoryTransaction:
    """
    Receive an approved PO in one action, closing it.

    Receiving is a single event, mirroring how a transfer is received: `received`
    maps each line to the quantity that actually arrived, and whatever was not
    received is recorded as **short** (the remainder is not left outstanding for a
    later delivery — the PO closes). A line that arrived over its ordered quantity
    is an **excess**. Either way the difference is a variance, and — exactly as on
    a transfer receipt — a line whose received quantity differs from what was
    ordered must carry a `reasons[line_id]` note, or the receipt is refused.

    The one move whose consequence runs *before* the assignment: the guard is
    asked up front, against the same map, so a declined order cannot get as far as
    moving stock and then be refused.
    """
    if purchase_order.id is not None:
        purchase_order = await _lock_purchase_order(db, purchase_order.id)
    assert_can_transition_purchase_order(purchase_order, PurchaseOrderStatusEnum.CLOSED)

    branch = await db.get(Branch, purchase_order.branch_id)
    if branch is None:
        raise NotFoundError("Branch not found")
    business_date = await business_day_service.current_business_date(db, branch)

    reasons = reasons or {}

    # A discrepant line needs a reason before anything moves — collected up front
    # so the message names every one at once rather than failing line by line.
    missing: list[str] = []
    for po_item in purchase_order.items:
        quantity = _q(received.get(po_item.id, 0))
        if quantity != _q(po_item.quantity):
            reason = reasons.get(po_item.id)
            if not reason or not reason.strip():
                item = await db.get(InventoryItem, po_item.item_id)
                missing.append(getattr(item, "name", None) or str(po_item.item_id))
    if missing:
        raise BadRequestError(
            "A reason is required where the received quantity differs from what was "
            "ordered: " + ", ".join(missing)
        )

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
    paid_tax = Decimal("0")
    for po_item in purchase_order.items:
        quantity = _q(received.get(po_item.id, 0))
        # Record what arrived and the variance note (one-shot receipt: this is the
        # final received quantity, whether short, exact or over).
        po_item.received_quantity = quantity
        ordered = Decimal(str(po_item.quantity or 0))
        po_item.variance_reason = (
            reasons.get(po_item.id, "").strip() or None
            if quantity != _q(ordered)
            else None
        )
        if quantity <= 0:
            continue
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
        # Carry the recoverable VAT for the portion received, pro rata, onto the
        # ledger transaction so the reclaim report can read it (the cost itself
        # is gross and lands in the FIFO layer via unit_cost above). The invoice's
        # VAT is fixed at the ordered quantity, so an over-receipt is capped at the
        # ordered amount — receiving extra stock does not reclaim extra VAT.
        if ordered > 0:
            reclaimable = min(quantity, ordered)
            paid_tax += _money(
                Decimal(str(po_item.vat_amount or 0)) * (reclaimable / ordered)
            )

    if not any_line:
        raise BadRequestError("Nothing was received")
    transaction.paid_tax = _money(paid_tax)

    await db.flush()
    await db.refresh(transaction)
    posted = await post_transaction(db, transaction=transaction, user=user)

    # One-shot: the receipt closes the order, whatever was short.
    await transition_purchase_order(
        db, purchase_order, PurchaseOrderStatusEnum.CLOSED, user=user
    )
    await db.flush()
    return posted


# ─── Depletion from sales ─────────────────────────────────────────────────────


async def deplete_for_order(
    db: AsyncSession, *, order: Order, user: User
) -> InventoryTransaction | None:
    """
    Freeze and consume inventory for a finalized order in MM acceptance order.

    The durable source event owns idempotency and recipe history. A missing
    recipe is recorded as an exception without blocking the sale.
    """
    from app.services.inventory import source_event_service

    event_row = await source_event_service.accept_order(db, order=order, user=user)
    if event_row is None or event_row.transaction_id is None:
        return None
    return await db.get(InventoryTransaction, event_row.transaction_id)


async def restock_for_void(db: AsyncSession, *, order: Order, user: User) -> None:
    """
    Put a voided order's consumed ingredients back on the shelf.

    A counter sale posts a `CONSUMPTION_FROM_ORDERS` movement when it closes;
    voiding it after the fact reverses that movement in full. Delegates to the
    same return machinery the admin inventory-return endpoint uses, at a full
    `restock` disposition — which also resolves the disposition-required
    exception the cancellation logged against the frozen consumption. A no-op
    for an order that never consumed anything (inventory off, or no recipes),
    because `record_return` finds no original movement to reverse — and a no-op if
    the consumption was already reversed (e.g. a pre-packing cancellation beat the
    void here), since the FIFO return cap would otherwise raise on a second full
    return.
    """
    from app.services.inventory import source_event_service

    already_returned = await db.scalar(
        select(InventoryTransaction.id).where(
            InventoryTransaction.order_id == order.id,
            InventoryTransaction.type
            == InventoryTransactionTypeEnum.RETURN_FROM_ORDERS.value,
            InventoryTransaction.status == TransactionStatusEnum.CLOSED.value,
        )
    )
    if already_returned is not None:
        return
    await source_event_service.record_return(
        db,
        order=order,
        user=user,
        disposition="restock",
        proportion=Decimal("1"),
        idempotency_key=f"void:{order.id}",
        notes="Counter sale voided",
    )


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


async def record_waste(
    db: AsyncSession,
    *,
    branch: Branch,
    user: User,
    item_id: uuid.UUID,
    quantity: Decimal,
    from_production: bool = False,
    reason_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> InventoryTransaction:
    """
    Write off stock that was thrown away.

    Kept distinct from a quantity adjustment even though both reduce stock: a
    correction means the count was wrong, waste means the food is in the bin.
    Only the second belongs in a wastage cost report, so conflating them would
    hide the number a kitchen is actually managed on.
    """
    if quantity <= 0:
        raise BadRequestError("Waste quantity must be positive")

    kind = (
        InventoryTransactionTypeEnum.WASTE_FROM_PRODUCTION
        if from_production
        else InventoryTransactionTypeEnum.WASTE_FROM_ORDERS
    )
    business_date = await business_day_service.current_business_date(db, branch)
    item = await db.get(InventoryItem, item_id)
    if item is None:
        raise NotFoundError("Inventory item not found")

    transaction = InventoryTransaction(
        reference=await next_reference(db, kind.value),
        type=kind.value,
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
            # Positive: the waste transaction type already carries sign -1,
            # so negating here as well would put stock back on the shelf.
            quantity=_q(abs(quantity)),
            unit="storage",
            conversion_factor=Decimal(str(item.storage_to_ingredient_factor or 1)),
            unit_cost=inventory_item_cost_for_unit(item, "storage"),
        )
    )
    await db.flush()
    await db.refresh(transaction)
    return await post_transaction(db, transaction=transaction, user=user)


async def adjust_cost(
    db: AsyncSession,
    *,
    branch: Branch,
    item_id: uuid.UUID,
    warehouse_id: uuid.UUID | None,
    new_average_cost: Decimal,
    user: User,
    notes: str | None = None,
) -> dict:
    """
    Restate what stock on hand is worth, without moving any of it.

    A supplier reprices, or a cost was keyed wrong, and the valuation is now
    misleading. Quantity is deliberately untouched — this only moves money,
    and the previous cost is returned so the caller can record the delta.
    """
    if new_average_cost < 0:
        raise BadRequestError("Cost cannot be negative")
    branch_id = branch.id
    business_date = await business_day_service.current_business_date(db, branch)

    stmt = select(InventoryLevel).where(InventoryLevel.item_id == item_id)
    if warehouse_id is not None:
        await assert_warehouse_for_branch(db, warehouse_id, branch_id)
        stmt = stmt.where(InventoryLevel.warehouse_id == warehouse_id)
    else:
        warehouse_id = (await default_warehouse(db, branch_id)).id
        stmt = stmt.where(InventoryLevel.warehouse_id == warehouse_id)
    level = (await db.execute(stmt)).scalars().first()
    if level is None:
        raise NotFoundError("This item has no stock level to revalue")

    previous = Decimal(str(level.average_cost or 0))
    quantity = Decimal(str(level.quantity or 0))
    # Booked as a transaction, not just a field write. Its type carries sign 0
    # so posting it cannot move stock, but it leaves the trail the cost
    # adjustment report reads — a revaluation that only edits a column is
    # invisible the moment anyone asks why the valuation changed.
    transaction = InventoryTransaction(
        reference=await next_reference(
            db, InventoryTransactionTypeEnum.COST_ADJUSTMENT.value
        ),
        type=InventoryTransactionTypeEnum.COST_ADJUSTMENT.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=branch_id,
        warehouse_id=level.warehouse_id,
        business_date=business_date,
        notes=notes,
        creator_id=user.id,
        # Seed the collection so the line append does not trigger an implicit
        # selectin load on the flushed transaction (MissingGreenlet under async).
        items=[
            InventoryTransactionItem(
                item_id=item_id,
                quantity=_q(quantity),
                unit="storage",
                conversion_factor=Decimal("1"),
                unit_cost=_c(new_average_cost),
            )
        ],
    )
    db.add(transaction)
    await db.flush()
    await post_transaction(db, transaction=transaction, user=user)

    return {
        "item_id": str(item_id),
        "warehouse_id": str(level.warehouse_id) if level.warehouse_id else None,
        "quantity_on_hand": quantity,
        "previous_average_cost": previous,
        "new_average_cost": new_average_cost,
        # What the revaluation did to the books, which is the point of it.
        "value_change": (new_average_cost - previous) * quantity,
        "adjusted_by": user.display_name or user.email,
        "notes": notes,
    }


async def open_count(
    db: AsyncSession,
    *,
    branch: Branch,
    user: User,
    warehouse_id: uuid.UUID | None,
    item_ids: list[uuid.UUID] | None,
    notes: str | None = None,
) -> InventoryTransaction:
    """
    Start a stock count and freeze what the system currently believes.

    The system quantity is captured now, not at close, because a count can
    take an hour and sales keep happening. Comparing tonight's count against
    a figure that moved while counting would manufacture a variance nobody
    can explain.
    """
    warehouse = (
        (await assert_warehouse_for_branch(db, warehouse_id, branch.id)).id
        if warehouse_id
        else (await default_warehouse(db, branch.id)).id
    )
    transaction = InventoryTransaction(
        reference=await next_reference(
            db, InventoryTransactionTypeEnum.INVENTORY_COUNT.value
        ),
        type=InventoryTransactionTypeEnum.INVENTORY_COUNT.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=branch.id,
        warehouse_id=warehouse,
        business_date=await business_day_service.current_business_date(db, branch),
        notes=notes,
        creator_id=user.id,
    )
    db.add(transaction)
    await db.flush()

    stmt = select(InventoryLevel).where(InventoryLevel.warehouse_id == warehouse)
    if item_ids:
        stmt = stmt.where(InventoryLevel.item_id.in_(item_ids))
    for level in (await db.execute(stmt)).scalars().all():
        db.add(
            InventoryTransactionItem(
                transaction_id=transaction.id,
                item_id=level.item_id,
                # Zero until counted; the frozen system figure rides along as
                # the unit cost slot's sibling on the level itself.
                quantity=Decimal("0"),
                unit="storage",
                conversion_factor=Decimal("1"),
                unit_cost=_c(level.average_cost),
                expected_quantity=_q(level.quantity),
            )
        )
    await db.flush()
    await db.refresh(transaction)
    return transaction


async def close_count(
    db: AsyncSession,
    *,
    transaction: InventoryTransaction,
    user: User,
    counted: dict[uuid.UUID, Decimal],
) -> dict:
    """
    Post a count: write the variance to stock and report it line by line.

    The movement posted is the difference, not the counted figure — posting
    the count itself would add a second copy of everything on the shelf.
    """
    if transaction.type != InventoryTransactionTypeEnum.INVENTORY_COUNT.value:
        raise BadRequestError("That transaction is not a stock count")
    if transaction.status != TransactionStatusEnum.DRAFT.value:
        raise ConflictError("This count is already closed")

    # Write the counted balance, not the variance. post_transaction already
    # treats a count line as "what is actually on the shelf" and works out the
    # movement itself — computing the difference here too subtracted it twice
    # and left the stock sitting at the variance.
    from app.services.inventory import source_event_service

    await source_event_service.lock_branch_inventory(db, transaction.branch_id)
    systems, costs = {}, {}
    for line in transaction.items:
        level = await level_for(
            db, line.item_id, transaction.warehouse_id, for_update=True
        )
        systems[line.item_id] = Decimal(str(level.quantity or 0))
        costs[line.item_id] = Decimal(str(level.average_cost or 0))
        line.quantity = _q(
            Decimal(str(counted.get(line.item_id, systems[line.item_id])))
        )

    await db.flush()
    await db.refresh(transaction)
    posted = await post_transaction(db, transaction=transaction, user=user)

    lines, total_value = [], Decimal("0")
    for line in posted.items:
        system = systems.get(line.item_id, Decimal("0"))
        actual = Decimal(str(line.quantity))
        variance = actual - system
        value = variance * costs.get(line.item_id, Decimal("0"))
        total_value += value
        lines.append(
            {
                "item_id": str(line.item_id),
                "system_quantity": _q(system),
                "counted_quantity": _q(actual),
                "variance": _q(variance),
                "value_change": _money(value),
            }
        )
    return {
        "reference": posted.reference,
        "status": posted.status,
        "lines": lines,
        "total_value_change": _money(total_value),
    }
