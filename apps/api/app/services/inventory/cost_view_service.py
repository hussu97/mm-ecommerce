"""
What an item's stock is worth, explained — the read side of the cost popup.

Two views over the v3 costing projection (`costing_engine`):

* :func:`cost_layers` — the FIFO layers that make up the stock on hand right
  now, oldest (next to be used) first, each with where its quantity and its cost
  came from. The layers always add up to the stock on hand; the response says so
  explicitly, so a screen can flag the day they do not.
* :func:`cost_history` — every ledger line that moved the item at a branch, with
  what it is worth *now* (``inventory_line_costs``) beside what it was booked at,
  and the running quantity, value and average after it.
"""

from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import (
    InventoryCostLayer,
    InventoryItem,
    InventoryLevel,
    InventoryLineCost,
    InventoryTransaction,
    InventoryTransactionItem,
    PurchaseOrder,
    Warehouse,
)
from app.models.user import User
from app.schemas.inventory import (
    CostLayerResponse,
    ItemCostHistoryResponse,
    ItemCostHistoryRow,
    ItemCostLayersResponse,
)
from app.services import crud_service
from app.services.inventory import access_service

__all__ = ["cost_layers", "cost_history"]

_FOUR_DP = Decimal("0.0001")


def _is_unrestricted(user: User) -> bool:
    return bool(user.is_admin or (user.role and user.role.is_super_admin))


async def _references(
    db: AsyncSession, line_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Human reference per ledger line: its PO number when it has one, else the
    transaction reference (PUR-, CNT-, PRD-, TRR-…)."""
    if not line_ids:
        return {}
    rows = await db.execute(
        select(
            InventoryTransactionItem.id,
            InventoryTransaction.reference,
            PurchaseOrder.reference,
        )
        .join(
            InventoryTransaction,
            InventoryTransaction.id == InventoryTransactionItem.transaction_id,
        )
        .outerjoin(
            PurchaseOrder, PurchaseOrder.id == InventoryTransaction.purchase_order_id
        )
        .where(InventoryTransactionItem.id.in_(line_ids))
    )
    return {line_id: po_ref or ref for line_id, ref, po_ref in rows.all()}


async def cost_layers(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    branch_id: uuid.UUID | None,
    user: User,
) -> ItemCostLayersResponse:
    await crud_service.get_or_404(db, InventoryItem, item_id, include_deleted=True)
    layer_stmt = (
        select(InventoryCostLayer, Warehouse.name, InventoryTransaction.posted_at)
        .join(Warehouse, Warehouse.id == InventoryCostLayer.warehouse_id)
        .join(
            InventoryTransaction,
            InventoryTransaction.id == InventoryCostLayer.source_transaction_id,
        )
        .where(
            InventoryCostLayer.item_id == item_id,
            InventoryCostLayer.remaining_quantity > 0,
        )
        .order_by(
            InventoryCostLayer.warehouse_id,
            InventoryCostLayer.posting_sequence,
            InventoryCostLayer.layer_index,
        )
        # The projection is rewritten through Core; never trust a cached entity.
        .execution_options(populate_existing=True)
    )
    level_stmt = (
        select(func.coalesce(func.sum(func.greatest(InventoryLevel.quantity, 0)), 0))
        .join(Warehouse, Warehouse.id == InventoryLevel.warehouse_id)
        .where(InventoryLevel.item_id == item_id)
    )
    if branch_id:
        await access_service.assert_branch_access(db, user, branch_id)
        layer_stmt = layer_stmt.where(InventoryCostLayer.branch_id == branch_id)
        level_stmt = level_stmt.where(Warehouse.branch_id == branch_id)
    elif not _is_unrestricted(user):
        # A branch-restricted user must not read every branch's purchase costs.
        layer_stmt = layer_stmt.where(
            InventoryCostLayer.branch_id.in_(access_service.branch_ids_for(user))
        )
        level_stmt = level_stmt.where(
            Warehouse.branch_id.in_(access_service.branch_ids_for(user))
        )
    rows = list((await db.execute(layer_stmt)).all())
    on_hand = Decimal(str(await db.scalar(level_stmt) or 0))

    references = await _references(
        db,
        {layer.source_line_id for layer, _, _ in rows}
        | {layer.priced_by_line_id for layer, _, _ in rows if layer.priced_by_line_id},
    )
    layers: list[CostLayerResponse] = []
    total_qty = Decimal("0")
    total_value = Decimal("0")
    seen_warehouses: set[uuid.UUID] = set()
    for layer, warehouse_name, posted_at in rows:
        remaining = Decimal(str(layer.remaining_quantity))
        line_value = (remaining * Decimal(str(layer.unit_cost))).quantize(
            _FOUR_DP, rounding=ROUND_HALF_UP
        )
        total_qty += remaining
        total_value += line_value
        response = CostLayerResponse.model_validate(layer)
        response.warehouse_name = warehouse_name
        response.line_value = line_value
        response.posted_at = posted_at
        response.source_reference = references.get(layer.source_line_id)
        if layer.priced_by_line_id and layer.priced_by_line_id != layer.source_line_id:
            response.cost_source_reference = references.get(layer.priced_by_line_id)
        response.next_out = layer.warehouse_id not in seen_warehouses
        seen_warehouses.add(layer.warehouse_id)
        layers.append(response)
    average = (
        (total_value / total_qty).quantize(Decimal("0.000001"))
        if total_qty > 0
        else Decimal("0")
    )
    return ItemCostLayersResponse(
        item_id=item_id,
        branch_id=branch_id,
        total_quantity=total_qty,
        total_value=total_value,
        average_cost=average,
        on_hand_quantity=on_hand,
        layers_match_stock=total_qty == on_hand,
        layers=layers,
    )


async def cost_history(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    branch_id: uuid.UUID,
    user: User,
    page: int = 1,
    per_page: int = 50,
) -> ItemCostHistoryResponse:
    await crud_service.get_or_404(db, InventoryItem, item_id, include_deleted=True)
    await access_service.assert_branch_access(db, user, branch_id)
    lc, t, i = InventoryLineCost, InventoryTransaction, InventoryTransactionItem
    scope = (lc.item_id == item_id, lc.branch_id == branch_id)
    total = int(
        await db.scalar(select(func.count()).select_from(lc).where(*scope)) or 0
    )
    rows = (
        await db.execute(
            select(lc, t.reference, t.type, t.posted_at, t.business_date, i.total_cost)
            .join(t, t.id == lc.transaction_id)
            .join(i, i.id == lc.line_id)
            .where(*scope)
            .order_by(lc.posting_sequence.desc(), lc.line_id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).all()
    references = await _references(
        db, {row[0].priced_by_line_id for row in rows if row[0].priced_by_line_id}
    )
    items: list[ItemCostHistoryRow] = []
    for cost, reference, type_, posted_at, business_date, booked in rows:
        running_qty = Decimal(str(cost.running_quantity))
        running_value = Decimal(str(cost.running_value))
        total_cost = Decimal(str(cost.total_cost))
        booked_total = Decimal(str(booked or 0))
        items.append(
            ItemCostHistoryRow(
                line_id=cost.line_id,
                transaction_id=cost.transaction_id,
                reference=reference,
                type=type_,
                posted_at=posted_at,
                business_date=business_date,
                quantity=Decimal(str(cost.quantity)),
                unit_cost=Decimal(str(cost.unit_cost)),
                total_cost=total_cost,
                booked_total_cost=booked_total if booked_total != total_cost else None,
                is_provisional=cost.is_provisional,
                superseded=cost.superseded,
                cost_source_reference=(
                    references.get(cost.priced_by_line_id)
                    if cost.priced_by_line_id and cost.priced_by_line_id != cost.line_id
                    else None
                ),
                running_quantity=running_qty,
                running_value=running_value,
                running_average_cost=(
                    (running_value / running_qty).quantize(Decimal("0.000001"))
                    if running_qty > 0
                    else None
                ),
            )
        )
    return ItemCostHistoryResponse(
        item_id=item_id,
        branch_id=branch_id,
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=max(1, (total + per_page - 1) // per_page),
    )
