"""Seed stock the way production does: through the ledger.

Under FIFO costing v3 the ledger is the only source of stock — a level written
directly, with no posting behind it, is drift the next replay restates to what
the ledger says. Fixtures that need stock on hand post an opening balance.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.models.inventory import (
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    TransactionStatusEnum,
)
from app.services.inventory import inventory_service


async def seed_stock(
    db,
    *,
    branch_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    item_id: uuid.UUID,
    quantity: Decimal | str,
    unit_cost: Decimal | str = "0",
    business_date: str = "2026-01-01",
) -> InventoryTransaction:
    """Post an opening balance that sets *item* to *quantity* at *unit_cost*."""
    transaction = InventoryTransaction(
        reference=await inventory_service.next_reference(
            db, InventoryTransactionTypeEnum.OPENING_BALANCE.value
        ),
        type=InventoryTransactionTypeEnum.OPENING_BALANCE.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=branch_id,
        warehouse_id=warehouse_id,
        business_date=business_date,
        items=[
            InventoryTransactionItem(
                item_id=item_id,
                quantity=Decimal(str(quantity)),
                unit="storage",
                conversion_factor=Decimal("1"),
                unit_cost=Decimal(str(unit_cost)),
            )
        ],
    )
    db.add(transaction)
    await db.flush()
    return await inventory_service.post_transaction(
        db, transaction=transaction, user=None
    )
