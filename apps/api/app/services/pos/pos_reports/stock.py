"""Cost adjustments, purchase orders and transfers."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    PurchaseOrder,
    PurchaseOrderItem,
    Supplier,
)


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
