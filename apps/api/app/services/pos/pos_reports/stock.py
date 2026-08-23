"""Inventory valuation, cost of goods, suppliers, purchase orders and transfers."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    PurchaseOrder,
    PurchaseOrderItem,
    Supplier,
    Warehouse,
)

from ._base import (
    ZERO,
)
from .sales import sales_summary

# ─── Inventory ────────────────────────────────────────────────────────────────


async def inventory_valuation(
    db: AsyncSession, *, branch_id: uuid.UUID | None = None
) -> dict:
    """Total value of stock on hand, and the items below their reorder point."""
    stmt = (
        select(InventoryLevel, InventoryItem)
        .join(InventoryItem, InventoryItem.id == InventoryLevel.item_id)
        .where(InventoryItem.deleted_at.is_(None))
    )
    if branch_id:
        stmt = stmt.join(Warehouse, Warehouse.id == InventoryLevel.warehouse_id).where(
            Warehouse.branch_id == branch_id
        )

    total_value = ZERO
    below: list[dict] = []
    item_count = 0
    for level, item in (await db.execute(stmt)).all():
        item_count += 1
        total_value += Decimal(str(level.quantity)) * Decimal(str(level.average_cost))
        if Decimal(str(level.quantity)) < Decimal(str(item.minimum_level)):
            below.append(
                {
                    "item_id": str(item.id),
                    "sku": item.sku,
                    "name": item.name,
                    "quantity": money(level.quantity),
                    "minimum_level": money(item.minimum_level),
                    "par_level": money(item.par_level),
                    "unit": item.ingredient_unit,
                    "shortfall": money(
                        Decimal(str(item.par_level)) - Decimal(str(level.quantity))
                    ),
                }
            )

    return {
        "items_tracked": item_count,
        "total_value": money(total_value),
        "below_minimum_count": len(below),
        "below_minimum": below,
    }


async def cost_of_goods(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """
    COGS from the depletion ledger, and the resulting margin against net sales.
    Sourced from posted consumption transactions rather than from recipes, so it
    reflects what was actually taken out of stock.
    """
    stmt = (
        select(func.coalesce(func.sum(InventoryTransactionItem.total_cost), 0))
        .select_from(InventoryTransactionItem)
        .join(
            InventoryTransaction,
            InventoryTransaction.id == InventoryTransactionItem.transaction_id,
        )
        .where(
            InventoryTransaction.type
            == InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value,
            InventoryTransaction.status == "closed",
        )
    )
    if branch_id:
        stmt = stmt.where(InventoryTransaction.branch_id == branch_id)
    if date_from:
        stmt = stmt.where(InventoryTransaction.business_date >= date_from)
    if date_to:
        stmt = stmt.where(InventoryTransaction.business_date <= date_to)

    cogs = money((await db.execute(stmt)).scalar_one())
    sales = await sales_summary(
        db, branch_id=branch_id, date_from=date_from, date_to=date_to
    )
    net = sales["net_sales_excl_tax"]
    margin = money(net - cogs)
    return {
        "cost_of_goods": cogs,
        "net_sales_excl_tax": net,
        "gross_margin": margin,
        "gross_margin_percent": (round(float(margin / net) * 100, 2) if net else 0.0),
    }


async def suppliers_analysis(
    db: AsyncSession,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """What each supplier has been paid, and how much was ordered from them."""
    stmt = (
        select(
            Supplier.name,
            func.count(func.distinct(PurchaseOrder.id)),
            func.coalesce(func.sum(PurchaseOrder.total_cost), 0),
        )
        .select_from(PurchaseOrder)
        .join(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .group_by(Supplier.name)
        .order_by(func.coalesce(func.sum(PurchaseOrder.total_cost), 0).desc())
        .limit(limit)
    )
    if date_from:
        stmt = stmt.where(PurchaseOrder.business_date >= date_from)
    if date_to:
        stmt = stmt.where(PurchaseOrder.business_date <= date_to)

    rows = (await db.execute(stmt)).all()
    return [
        {
            "supplier": name,
            "purchase_orders": int(count or 0),
            "total_spend": money(total),
        }
        for name, count, total in rows
    ]


async def cost_adjustment_history(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """
    Every stock revaluation, newest first.

    Cost adjustments move money without moving stock, so they never show up in
    a quantity report — which is exactly why they need their own trail.
    """
    stmt = (
        select(
            InventoryTransaction.reference,
            InventoryTransaction.business_date,
            InventoryTransaction.notes,
            InventoryItem.name,
            InventoryItem.sku,
            InventoryTransactionItem.quantity,
            InventoryTransactionItem.unit_cost,
            InventoryTransactionItem.total_cost,
        )
        .select_from(InventoryTransaction)
        .join(
            InventoryTransactionItem,
            InventoryTransactionItem.transaction_id == InventoryTransaction.id,
        )
        .join(InventoryItem, InventoryItem.id == InventoryTransactionItem.item_id)
        .where(
            InventoryTransaction.type.in_(
                [
                    InventoryTransactionTypeEnum.COST_ADJUSTMENT.value,
                    InventoryTransactionTypeEnum.WASTE_FROM_ORDERS.value,
                    InventoryTransactionTypeEnum.WASTE_FROM_PRODUCTION.value,
                    InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value,
                ]
            )
        )
        .order_by(InventoryTransaction.created_at.desc())
        .limit(limit)
    )
    if branch_id:
        stmt = stmt.where(InventoryTransaction.branch_id == branch_id)
    if date_from:
        stmt = stmt.where(InventoryTransaction.business_date >= date_from)
    if date_to:
        stmt = stmt.where(InventoryTransaction.business_date <= date_to)

    rows = (await db.execute(stmt)).all()
    return [
        {
            "reference": ref,
            "business_date": day,
            "item": name,
            "sku": sku,
            "quantity": money(qty),
            "unit_cost": money(unit),
            "total_cost": money(total),
            "notes": notes,
        }
        for ref, day, notes, name, sku, qty, unit, total in rows
    ]


async def purchase_orders_report(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """
    Purchase orders with their supplier and what is still outstanding.

    Ordered value and received value are reported separately: the difference
    is what has been ordered and not yet arrived, which is the number a chef
    chases a supplier about.
    """
    received = func.coalesce(
        func.sum(PurchaseOrderItem.received_quantity * PurchaseOrderItem.unit_cost), 0
    )
    ordered = func.coalesce(
        func.sum(PurchaseOrderItem.quantity * PurchaseOrderItem.unit_cost), 0
    )

    stmt = (
        select(
            PurchaseOrder.reference,
            PurchaseOrder.status,
            PurchaseOrder.business_date,
            Supplier.name,
            func.count(PurchaseOrderItem.id),
            ordered,
            received,
        )
        .select_from(PurchaseOrder)
        .outerjoin(
            PurchaseOrderItem,
            PurchaseOrderItem.purchase_order_id == PurchaseOrder.id,
        )
        .outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .group_by(
            PurchaseOrder.reference,
            PurchaseOrder.status,
            PurchaseOrder.business_date,
            Supplier.name,
        )
        .order_by(PurchaseOrder.business_date.desc())
        .limit(limit)
    )
    if branch_id:
        stmt = stmt.where(PurchaseOrder.branch_id == branch_id)
    if date_from:
        stmt = stmt.where(PurchaseOrder.business_date >= date_from)
    if date_to:
        stmt = stmt.where(PurchaseOrder.business_date <= date_to)

    rows = (await db.execute(stmt)).all()
    return [
        {
            "reference": ref,
            "status": status,
            "business_date": day,
            "supplier": supplier or "Unknown",
            "lines": int(lines or 0),
            "ordered_value": money(ordered_value),
            "received_value": money(received_value),
            "outstanding_value": money(
                Decimal(str(ordered_value or 0)) - Decimal(str(received_value or 0))
            ),
        }
        for ref, status, day, supplier, lines, ordered_value, received_value in rows
    ]


async def transfers_report(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """
    Stock moved between branches, valued at the cost it left at.

    Both legs of a transfer are shown — sends and receives are separate
    transactions, and a send with no matching receive is stock in transit
    that somebody needs to chase.
    """
    stmt = (
        select(
            InventoryTransaction.reference,
            InventoryTransaction.type,
            InventoryTransaction.status,
            InventoryTransaction.business_date,
            Branch.name,
            func.count(InventoryTransactionItem.id),
            func.coalesce(func.sum(InventoryTransactionItem.total_cost), 0),
        )
        .select_from(InventoryTransaction)
        .outerjoin(
            InventoryTransactionItem,
            InventoryTransactionItem.transaction_id == InventoryTransaction.id,
        )
        .outerjoin(Branch, Branch.id == InventoryTransaction.branch_id)
        .where(
            InventoryTransaction.type.in_(
                [
                    InventoryTransactionTypeEnum.TRANSFER_SEND.value,
                    InventoryTransactionTypeEnum.TRANSFER_RECEIVE.value,
                ]
            )
        )
        .group_by(
            InventoryTransaction.reference,
            InventoryTransaction.type,
            InventoryTransaction.status,
            InventoryTransaction.business_date,
            Branch.name,
        )
        .order_by(InventoryTransaction.business_date.desc())
        .limit(limit)
    )
    if branch_id:
        stmt = stmt.where(InventoryTransaction.branch_id == branch_id)
    if date_from:
        stmt = stmt.where(InventoryTransaction.business_date >= date_from)
    if date_to:
        stmt = stmt.where(InventoryTransaction.business_date <= date_to)

    rows = (await db.execute(stmt)).all()
    return [
        {
            "reference": ref,
            "direction": "out" if kind.endswith("send") else "in",
            "status": status,
            "business_date": day,
            "branch": branch or "Unknown",
            "lines": int(lines or 0),
            "value": money(value),
        }
        for ref, kind, status, day, branch, lines, value in rows
    ]
