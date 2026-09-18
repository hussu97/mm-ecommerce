"""
FIFO cost layers — the valuation half of the inventory ledger.

Costing is **first-in, first-out**. Every inbound movement lays down an
`InventoryCostLayer` (a quantity at a unit cost); every issue draws from the
oldest layers first, ordered by ``posting_sequence`` then ``layer_index``, and
records one `InventoryCostLayerConsumption` per layer it touches. So a sale's
cost of goods is the *actual* cost of the specific receipts it consumed, not a
blended average.

``InventoryLevel.average_cost`` is then a **derived** figure — the weighted
average of what still remains (Σ remaining × cost ÷ Σ remaining) — kept only so
every existing reader (reports, valuation) works unchanged.

Everything here is a projection of the immutable ledger and runs inside
`inventory_service.post_transaction`, under the branch advisory lock it already
holds. Nothing here commits.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money as _money
from app.core.money import quantity as _q
from app.core.money import unit_cost as _c
from app.models.base import utcnow
from app.models.inventory import (
    CostLayerSourceKindEnum,
    InventoryCostLayer,
    InventoryCostLayerConsumption,
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
)

__all__ = [
    "receipt_layer_kind",
    "create_layer",
    "consume_fifo",
    "oldest_active_layer_cost",
    "rescale_layers_to_average",
    "remaining_totals",
    "derive_average_cost",
    "refresh_level_average",
    "delete_branch_layers",
]


#: Inbound transaction types that lay down a normal receipt layer. Positive
#: adjustments and count overages are handled separately (priced off the oldest
#: surviving layer), and reversals restore layers rather than create them.
_RECEIPT_LAYER_KIND: dict[str, CostLayerSourceKindEnum] = {
    InventoryTransactionTypeEnum.PURCHASING.value: CostLayerSourceKindEnum.PURCHASING,
    InventoryTransactionTypeEnum.TRANSFER_RECEIVE.value: (
        CostLayerSourceKindEnum.TRANSFER_RECEIVE
    ),
    InventoryTransactionTypeEnum.PRODUCTION.value: CostLayerSourceKindEnum.PRODUCTION,
    InventoryTransactionTypeEnum.RETURN_FROM_ORDERS.value: (
        CostLayerSourceKindEnum.RETURN_FROM_ORDERS
    ),
    InventoryTransactionTypeEnum.OPENING_BALANCE.value: (
        CostLayerSourceKindEnum.OPENING_BALANCE
    ),
}


def receipt_layer_kind(transaction_type: str) -> CostLayerSourceKindEnum | None:
    """The layer kind a plain receipt of *transaction_type* lays down, if any."""
    return _RECEIPT_LAYER_KIND.get(transaction_type)


async def _active_layers(
    db: AsyncSession,
    item_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> list[InventoryCostLayer]:
    """Surviving layers for one (item, warehouse), oldest first — the FIFO queue."""
    stmt = (
        select(InventoryCostLayer)
        .where(
            InventoryCostLayer.item_id == item_id,
            InventoryCostLayer.warehouse_id == warehouse_id,
            InventoryCostLayer.remaining_quantity > 0,
        )
        .order_by(
            InventoryCostLayer.posting_sequence,
            InventoryCostLayer.layer_index,
        )
    )
    if for_update:
        stmt = stmt.with_for_update()
    return list((await db.execute(stmt)).scalars().all())


async def remaining_totals(
    db: AsyncSession, item_id: uuid.UUID, warehouse_id: uuid.UUID
) -> tuple[Decimal, Decimal]:
    """(Σ remaining quantity, Σ remaining value) over surviving layers."""
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(InventoryCostLayer.remaining_quantity), 0),
                func.coalesce(
                    func.sum(
                        InventoryCostLayer.remaining_quantity
                        * InventoryCostLayer.unit_cost
                    ),
                    0,
                ),
            ).where(
                InventoryCostLayer.item_id == item_id,
                InventoryCostLayer.warehouse_id == warehouse_id,
                InventoryCostLayer.remaining_quantity > 0,
            )
        )
    ).one()
    return _q(row[0]), Decimal(str(row[1]))


async def oldest_active_layer_cost(
    db: AsyncSession, item_id: uuid.UUID, warehouse_id: uuid.UUID
) -> Decimal | None:
    """
    Unit cost of the earliest surviving layer.

    The price a positive adjustment or count overage is booked at — "the
    earliest available cost of a purchase whose stock is still in the system".
    """
    layers = await _active_layers(db, item_id, warehouse_id)
    return _c(layers[0].unit_cost) if layers else None


async def derive_average_cost(
    db: AsyncSession,
    item: InventoryItem,
    warehouse_id: uuid.UUID,
    *,
    fallback: Decimal | None = None,
) -> Decimal:
    """Weighted average of the surviving layers, per storage unit.

    Falls back to the given value (usually the level's current average), then to
    the item's catalogue cost, when nothing remains costed — a level can sit at
    negative quantity with no layers, and a zero average there is a lie.
    """
    total_qty, total_value = await remaining_totals(db, item.id, warehouse_id)
    if total_qty > 0:
        return _c(total_value / total_qty)
    if fallback is not None:
        return _c(fallback)
    return _c(item.cost or 0)


async def refresh_level_average(
    db: AsyncSession, level: InventoryLevel, item: InventoryItem
) -> None:
    """Restate ``level.average_cost`` from the surviving layers."""
    level.average_cost = await derive_average_cost(
        db, item, level.warehouse_id, fallback=Decimal(str(level.average_cost or 0))
    )


async def create_layer(
    db: AsyncSession,
    *,
    transaction: InventoryTransaction,
    line: InventoryTransactionItem,
    item: InventoryItem,
    warehouse_id: uuid.UUID,
    quantity: Decimal,
    unit_cost: Decimal,
    source_kind: CostLayerSourceKindEnum,
    layer_index: int,
) -> InventoryCostLayer:
    """Lay down one FIFO layer for an inbound movement line."""
    layer = InventoryCostLayer(
        item_id=item.id,
        warehouse_id=warehouse_id,
        branch_id=transaction.branch_id,
        source_transaction_id=transaction.id,
        source_line_id=line.id,
        purchase_order_id=transaction.purchase_order_id,
        source_kind=source_kind.value,
        posting_sequence=transaction.posting_sequence,
        layer_index=layer_index,
        original_quantity=_q(quantity),
        remaining_quantity=_q(quantity),
        unit_cost=_c(unit_cost),
        received_at=utcnow(),
    )
    db.add(layer)
    await db.flush()
    return layer


async def backfill_uncosted_stock(
    db: AsyncSession,
    *,
    transaction: InventoryTransaction,
    line: InventoryTransactionItem,
    item: InventoryItem,
    warehouse_id: uuid.UUID,
    on_hand_before: Decimal,
    unit_cost: Decimal,
    layer_index: int,
) -> None:
    """
    Cost pre-existing stock that never had a layer, at the incoming price.

    When stock was on the shelf before FIFO began (or before this item was ever
    purchased), the level shows a quantity no layer accounts for. The first
    receipt that gives us a price seeds one ``backfill`` layer for exactly that
    gap — so the old stock is valued at what we now know it is worth, with no
    dispose-and-re-bring churn.
    """
    total_qty, _ = await remaining_totals(db, item.id, warehouse_id)
    gap = _q(Decimal(str(on_hand_before)) - total_qty)
    if gap <= 0:
        return
    await create_layer(
        db,
        transaction=transaction,
        line=line,
        item=item,
        warehouse_id=warehouse_id,
        quantity=gap,
        unit_cost=unit_cost,
        source_kind=CostLayerSourceKindEnum.BACKFILL,
        layer_index=layer_index,
    )


async def consume_fifo(
    db: AsyncSession,
    *,
    line: InventoryTransactionItem,
    item: InventoryItem,
    warehouse_id: uuid.UUID,
    quantity: Decimal,
    posting_sequence: int,
    fallback_cost: Decimal,
) -> Decimal:
    """
    Draw *quantity* (storage units, positive) from the oldest layers first.

    Writes one consumption row per layer touched and returns the true FIFO cost
    of the issue. If the layers run dry (a negative-stock branch), the remainder
    is costed at ``fallback_cost`` and recorded as a shortfall — a layer arriving
    later simply starts a fresh layer, and the level's negative quantity heals.
    """
    outstanding = _q(quantity)
    if outstanding <= 0:
        return Decimal("0.00")

    layers = await _active_layers(db, item.id, warehouse_id, for_update=True)
    total_cost = Decimal("0")
    # A never-costed item has a 0 fallback (its level average is 0); an over-issue
    # of it should still book COGS at the catalogue cost, not nothing.
    last_cost = fallback_cost if fallback_cost > 0 else _c(item.cost or 0)
    for layer in layers:
        if outstanding <= 0:
            break
        available = _q(layer.remaining_quantity)
        take = min(available, outstanding)
        if take <= 0:
            continue
        layer.remaining_quantity = _q(available - take)
        if layer.remaining_quantity <= 0:
            layer.exhausted_at = utcnow()
        last_cost = _c(layer.unit_cost)
        line_cost = _money(take * last_cost)
        db.add(
            InventoryCostLayerConsumption(
                consuming_line_id=line.id,
                layer_id=layer.id,
                item_id=item.id,
                warehouse_id=warehouse_id,
                quantity=take,
                unit_cost=last_cost,
                total_cost=line_cost,
                posting_sequence=posting_sequence,
            )
        )
        total_cost += line_cost
        outstanding = _q(outstanding - take)

    if outstanding > 0:
        # Issued more than any layer covered: cost the tail at the last price we
        # saw and flag it, so reports and the rebuild can see the uncosted gap.
        shortfall_cost = _money(outstanding * _c(last_cost))
        db.add(
            InventoryCostLayerConsumption(
                consuming_line_id=line.id,
                layer_id=None,
                item_id=item.id,
                warehouse_id=warehouse_id,
                quantity=outstanding,
                unit_cost=_c(last_cost),
                total_cost=shortfall_cost,
                posting_sequence=posting_sequence,
                is_shortfall=True,
            )
        )
        total_cost += shortfall_cost

    await db.flush()
    return _money(total_cost)


async def restore_consumed_line(
    db: AsyncSession,
    *,
    original_line_id: uuid.UUID,
    item: InventoryItem,
    warehouse_id: uuid.UUID,
) -> Decimal:
    """
    Undo an issue: put its consumed quantities back onto the exact layers.

    Walks the original line's consumption trail and adds each draw back to the
    layer it came from (re-opening an exhausted layer), so a void or return
    restores stock at precisely the cost it left at. Shortfall draws (no layer)
    carried no cost and need no layer restoration — the quantity itself returns
    through the level movement. Returns the value restored.
    """
    consumptions = (
        (
            await db.execute(
                select(InventoryCostLayerConsumption).where(
                    InventoryCostLayerConsumption.consuming_line_id == original_line_id
                )
            )
        )
        .scalars()
        .all()
    )
    restored_value = Decimal("0")
    for consumption in consumptions:
        restored_value += Decimal(str(consumption.total_cost or 0))
        if consumption.layer_id is None:
            continue
        layer = await db.get(InventoryCostLayer, consumption.layer_id)
        if layer is None:
            continue
        layer.remaining_quantity = _q(
            Decimal(str(layer.remaining_quantity or 0))
            + Decimal(str(consumption.quantity or 0))
        )
        if layer.remaining_quantity > 0:
            layer.exhausted_at = None
    await db.flush()
    return _money(restored_value)


async def reverse_receipt_line(
    db: AsyncSession,
    *,
    original_line_id: uuid.UUID,
    reversal_line: InventoryTransactionItem,
    item: InventoryItem,
    warehouse_id: uuid.UUID,
    quantity: Decimal,
    posting_sequence: int,
    fallback_cost: Decimal,
) -> Decimal:
    """
    Undo a receipt: remove its stock from the layers it created.

    Draws *quantity* back out of the layers whose ``source_line_id`` is the
    original receipt line (its own layer, plus any backfill it seeded). If that
    receipt's stock has already been (partly) consumed, the shortfall is booked
    like any negative-stock issue. Returns the value removed.
    """
    layers = (
        (
            await db.execute(
                select(InventoryCostLayer)
                .where(
                    InventoryCostLayer.source_line_id == original_line_id,
                    InventoryCostLayer.remaining_quantity > 0,
                )
                .order_by(InventoryCostLayer.layer_index)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    outstanding = _q(quantity)
    total_cost = Decimal("0")
    last_cost = fallback_cost
    for layer in layers:
        if outstanding <= 0:
            break
        take = min(_q(layer.remaining_quantity), outstanding)
        if take <= 0:
            continue
        layer.remaining_quantity = _q(Decimal(str(layer.remaining_quantity)) - take)
        if layer.remaining_quantity <= 0:
            layer.exhausted_at = utcnow()
        last_cost = _c(layer.unit_cost)
        line_cost = _money(take * last_cost)
        db.add(
            InventoryCostLayerConsumption(
                consuming_line_id=reversal_line.id,
                layer_id=layer.id,
                item_id=item.id,
                warehouse_id=warehouse_id,
                quantity=take,
                unit_cost=last_cost,
                total_cost=line_cost,
                posting_sequence=posting_sequence,
            )
        )
        total_cost += line_cost
        outstanding = _q(outstanding - take)

    if outstanding > 0:
        shortfall_cost = _money(outstanding * _c(last_cost))
        db.add(
            InventoryCostLayerConsumption(
                consuming_line_id=reversal_line.id,
                layer_id=None,
                item_id=item.id,
                warehouse_id=warehouse_id,
                quantity=outstanding,
                unit_cost=_c(last_cost),
                total_cost=shortfall_cost,
                posting_sequence=posting_sequence,
                is_shortfall=True,
            )
        )
        total_cost += shortfall_cost
    await db.flush()
    return _money(total_cost)


async def rescale_layers_to_average(
    db: AsyncSession,
    *,
    item: InventoryItem,
    warehouse_id: uuid.UUID,
    new_average: Decimal,
) -> Decimal:
    """
    Revalue surviving layers so their weighted average becomes *new_average*.

    A cost adjustment restates what stock is worth without moving any of it.
    Rescaling every layer by the same ratio hits the target valuation while
    keeping each layer's *relative* cost — so the FIFO spread survives a
    revaluation rather than being flattened to one price. When the layers hold no
    value to scale (all at 0), the target is applied absolutely instead. Returns
    the value change (new − old).
    """
    layers = await _active_layers(db, item.id, warehouse_id, for_update=True)
    total_qty = sum((_q(layer.remaining_quantity) for layer in layers), Decimal("0"))
    old_value = sum(
        (_q(layer.remaining_quantity) * _c(layer.unit_cost) for layer in layers),
        Decimal("0"),
    )
    if total_qty <= 0:
        return Decimal("0.00")
    target_average = _c(new_average)
    target_value = target_average * total_qty
    if old_value > 0:
        factor = target_value / old_value
        for layer in layers:
            layer.unit_cost = _c(Decimal(str(layer.unit_cost)) * factor)
    else:
        for layer in layers:
            layer.unit_cost = target_average
    await db.flush()
    return _money(target_value - old_value)


async def delete_branch_layers(
    db: AsyncSession, branch_id: uuid.UUID, warehouse_id: uuid.UUID
) -> None:
    """Drop layers + consumptions for one (branch, warehouse), for a rebuild.

    Scoped to the warehouse being rebuilt — a branch may hold several, and a
    rebuild replays only the one it was asked for, so it must never wipe another
    warehouse's layers. Consumptions are matched by the warehouse the consuming
    line moved (so shortfall rows, which carry no ``layer_id``, are cleared too);
    layers by their own denormalised branch + warehouse.
    """
    consuming_lines = (
        select(InventoryTransactionItem.id)
        .join(
            InventoryTransaction,
            InventoryTransaction.id == InventoryTransactionItem.transaction_id,
        )
        .where(
            InventoryTransaction.branch_id == branch_id,
            InventoryTransaction.warehouse_id == warehouse_id,
        )
    )
    await db.execute(
        InventoryCostLayerConsumption.__table__.delete().where(
            InventoryCostLayerConsumption.consuming_line_id.in_(consuming_lines)
        )
    )
    await db.execute(
        InventoryCostLayer.__table__.delete().where(
            InventoryCostLayer.branch_id == branch_id,
            InventoryCostLayer.warehouse_id == warehouse_id,
        )
    )
    await db.flush()
