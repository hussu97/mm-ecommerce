"""
Readers over the FIFO cost projection.

The layers, consumptions and per-line costs are written by one place only —
the v3 costing engine (`costing_engine`, persisted by `costing_service`). What
lives here is the read side every screen and report shares: an item's current
cost, estate-wide or at one warehouse.

``InventoryLevel.average_cost`` (per warehouse) is the figure to use whenever a
branch is in hand; the estate-wide blend below is for screens that genuinely
span every branch (the items catalogue's "all branches" column, the export).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import unit_cost as _c
from app.models.inventory import InventoryCostLayer, InventoryLevel

__all__ = [
    "item_average_cost",
    "item_average_costs",
    "warehouse_average_costs",
]


async def item_average_cost(db: AsyncSession, item_id: uuid.UUID) -> Decimal:
    """The item's current cost per storage unit across *all* warehouses — the
    weighted average of every surviving FIFO layer. 0 when the item has no costed
    stock yet."""
    return (await item_average_costs(db, [item_id])).get(item_id, _c(0))


async def item_average_costs(
    db: AsyncSession, item_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Decimal]:
    """Bulk :func:`item_average_cost` — one query for many items, for list and
    export screens. Items with no costed stock are omitted (read as 0)."""
    if not item_ids:
        return {}
    rows = (
        await db.execute(
            select(
                InventoryCostLayer.item_id,
                func.coalesce(func.sum(InventoryCostLayer.remaining_quantity), 0),
                func.coalesce(
                    func.sum(
                        InventoryCostLayer.remaining_quantity
                        * InventoryCostLayer.unit_cost
                    ),
                    0,
                ),
            )
            .where(
                InventoryCostLayer.item_id.in_(item_ids),
                InventoryCostLayer.remaining_quantity > 0,
            )
            .group_by(InventoryCostLayer.item_id)
        )
    ).all()
    out: dict[uuid.UUID, Decimal] = {}
    for item_id, qty, value in rows:
        q = Decimal(str(qty))
        if q > 0:
            out[item_id] = _c(Decimal(str(value)) / q)
    return out


async def warehouse_average_costs(
    db: AsyncSession, item_ids: list[uuid.UUID], warehouse_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Decimal]:
    """Each item's current cost across the given warehouses (one branch's).

    Weighted by what is on hand, falling back to the level's own average (the
    last known cost) when the branch has none of it left — so a recipe costed at
    a branch that has just run out still prices the ingredient at what it last
    cost there, not at zero.
    """
    if not item_ids or not warehouse_ids:
        return {}
    rows = (
        await db.execute(
            select(
                InventoryLevel.item_id,
                InventoryLevel.quantity,
                InventoryLevel.average_cost,
            ).where(
                InventoryLevel.item_id.in_(item_ids),
                InventoryLevel.warehouse_id.in_(warehouse_ids),
            )
        )
    ).all()
    weighted: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    fallback: dict[uuid.UUID, Decimal] = {}
    for item_id, qty, average in rows:
        qty = Decimal(str(qty or 0))
        average = Decimal(str(average or 0))
        if average > 0:
            fallback.setdefault(item_id, average)
        if qty > 0:
            total_qty, total_value = weighted.get(item_id, (Decimal(0), Decimal(0)))
            weighted[item_id] = (total_qty + qty, total_value + qty * average)
    out = {item_id: _c(avg) for item_id, avg in fallback.items()}
    for item_id, (qty, value) in weighted.items():
        out[item_id] = _c(value / qty)
    return out
