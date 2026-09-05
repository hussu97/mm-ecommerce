"""Immutable-ledger corrections and deterministic level projection rebuilds."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.money import quantity, unit_cost
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    TransactionStatusEnum,
)
from app.models.inventory_v2 import BranchInventorySettings
from app.models.user import User
from app.services.inventory import inventory_service, source_event_service
from app.services.pos import business_day_service


@dataclass(slots=True)
class ProjectionDrift:
    item_id: uuid.UUID
    warehouse_id: uuid.UUID
    cached_quantity: Decimal
    ledger_quantity: Decimal
    cached_average_cost: Decimal
    ledger_average_cost: Decimal
    through_sequence: int | None

    def as_dict(self) -> dict:
        return {
            "item_id": str(self.item_id),
            "warehouse_id": str(self.warehouse_id),
            "cached_quantity": quantity(self.cached_quantity),
            "ledger_quantity": quantity(self.ledger_quantity),
            "cached_average_cost": unit_cost(self.cached_average_cost),
            "ledger_average_cost": unit_cost(self.ledger_average_cost),
            "through_sequence": self.through_sequence,
        }


async def reconcile_levels(
    db: AsyncSession, *, branch_id: uuid.UUID, apply: bool = False
) -> list[ProjectionDrift]:
    branch = await db.get(Branch, branch_id)
    if branch is None:
        raise NotFoundError("Branch not found")
    await source_event_service.lock_branch_inventory(db, branch_id)
    warehouse = await inventory_service.default_warehouse(db, branch_id)

    stmt = (
        select(InventoryTransaction, InventoryTransactionItem)
        .join(
            InventoryTransactionItem,
            InventoryTransactionItem.transaction_id == InventoryTransaction.id,
        )
        .where(
            InventoryTransaction.branch_id == branch_id,
            InventoryTransaction.warehouse_id == warehouse.id,
            InventoryTransaction.status == TransactionStatusEnum.CLOSED.value,
        )
        .order_by(
            InventoryTransaction.posting_sequence,
            InventoryTransactionItem.id,
        )
    )
    quantities: dict[uuid.UUID, Decimal] = {}
    averages: dict[uuid.UUID, Decimal] = {}
    sequences: dict[uuid.UUID, int | None] = {}
    for transaction, line in (await db.execute(stmt)).all():
        delta = Decimal(str(line.signed_quantity or 0))
        previous_quantity = quantities.get(line.item_id, Decimal("0"))
        previous_average = averages.get(line.item_id, Decimal("0"))
        if delta > 0:
            factor = Decimal(str(line.conversion_factor or 1))
            incoming = Decimal(str(line.unit_cost or 0))
            if line.unit != "ingredient" and factor:
                incoming /= factor
            if previous_quantity <= 0:
                previous_average = incoming
            else:
                previous_average = (
                    previous_quantity * previous_average + delta * incoming
                ) / (previous_quantity + delta)
        quantities[line.item_id] = previous_quantity + delta
        averages[line.item_id] = previous_average
        sequences[line.item_id] = transaction.posting_sequence

    levels = {
        level.item_id: level
        for level in (
            await db.execute(
                select(InventoryLevel).where(
                    InventoryLevel.warehouse_id == warehouse.id
                )
            )
        )
        .scalars()
        .all()
    }
    item_ids = set(levels) | set(quantities)
    drifts: list[ProjectionDrift] = []
    for item_id in sorted(item_ids, key=str):
        level = levels.get(item_id)
        cached_quantity = Decimal(str(level.quantity if level else 0))
        cached_average = Decimal(str(level.average_cost if level else 0))
        ledger_quantity = quantity(quantities.get(item_id, Decimal("0")))
        ledger_average = unit_cost(averages.get(item_id, Decimal("0")))
        if cached_quantity != ledger_quantity or cached_average != ledger_average:
            drifts.append(
                ProjectionDrift(
                    item_id=item_id,
                    warehouse_id=warehouse.id,
                    cached_quantity=cached_quantity,
                    ledger_quantity=ledger_quantity,
                    cached_average_cost=cached_average,
                    ledger_average_cost=ledger_average,
                    through_sequence=sequences.get(item_id),
                )
            )
        if apply:
            if level is None:
                level = InventoryLevel(
                    item_id=item_id,
                    warehouse_id=warehouse.id,
                    quantity=ledger_quantity,
                    average_cost=ledger_average,
                )
                db.add(level)
            else:
                level.quantity = ledger_quantity
                level.average_cost = ledger_average
            level.projected_through_sequence = sequences.get(item_id)
            level.reconciled_at = utcnow()
    if apply:
        await db.flush()
    return drifts


async def reverse_transaction(
    db: AsyncSession,
    *,
    transaction_id: uuid.UUID,
    user: User,
    reason: str,
) -> InventoryTransaction:
    original = await inventory_service.load_transaction(db, transaction_id)
    if not original.is_posted:
        raise ConflictError("Only a closed transaction can be reversed")
    existing = (
        (
            await db.execute(
                select(InventoryTransaction).where(
                    InventoryTransaction.reverses_transaction_id == original.id
                )
            )
        )
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        return existing

    branch = await db.get(Branch, original.branch_id)
    if branch is None:
        raise NotFoundError("Branch not found")
    await source_event_service.lock_branch_inventory(db, original.branch_id)
    correction_group = original.correction_group_id or uuid.uuid4()
    reversal = InventoryTransaction(
        reference=await inventory_service.next_reference(
            db, InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value
        ),
        type=InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=original.branch_id,
        warehouse_id=original.warehouse_id,
        business_date=await business_day_service.current_business_date(db, branch),
        creator_id=user.id,
        reverses_transaction_id=original.id,
        correction_group_id=correction_group,
        source_type="correction",
        source_id=str(original.id),
        idempotency_key=f"reverse:{original.id}",
        notes=reason,
        items=[],
    )
    db.add(reversal)
    await db.flush()
    for line in original.items:
        reversal.items.append(
            InventoryTransactionItem(
                item_id=line.item_id,
                quantity=quantity(-Decimal(str(line.signed_quantity))),
                unit="ingredient",
                conversion_factor=Decimal("1"),
                unit_cost=unit_cost(line.unit_cost or 0),
                recipe_version_id=line.recipe_version_id,
                recipe_path=line.recipe_path or [],
                notes=f"Reversal of {original.reference}",
            )
        )
    await db.flush()
    return await inventory_service.post_transaction(db, transaction=reversal, user=user)


async def preview_stock_audit(db: AsyncSession, *, branch_id: uuid.UUID, rows) -> dict:
    branch = await db.get(Branch, branch_id)
    if branch is None:
        raise NotFoundError("Branch not found")
    warehouse = await inventory_service.default_warehouse(db, branch_id)
    seen: set[str] = set()
    payload = []
    for row in rows:
        errors: list[str] = []
        sku_key = row.sku.strip().casefold()
        if sku_key in seen:
            errors.append("Duplicate SKU")
        seen.add(sku_key)
        items = list(
            (
                await db.execute(
                    select(InventoryItem).where(
                        InventoryItem.sku.ilike(row.sku.strip()),
                        InventoryItem.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        item = items[0] if len(items) == 1 else None
        if not items:
            errors.append("Unknown SKU")
        elif len(items) > 1:
            errors.append("Ambiguous SKU")
        expected = None
        counted = quantity(row.counted_quantity)
        delta = None
        if Decimal(str(row.counted_quantity)) != counted:
            errors.append("Quantity has more than four decimal places")
        if item:
            level = await inventory_service.level_for(db, item.id, warehouse.id)
            expected = quantity(level.quantity)
            normalised = (
                quantity(counted * Decimal(str(item.storage_to_ingredient_factor)))
                if row.unit == "storage"
                else counted
            )
            delta = quantity(normalised - expected)
            if abs(delta) > max(abs(expected) * Decimal("10"), Decimal("100000")):
                errors.append("Extreme variance requires manual review")
        payload.append(
            {
                "sku": row.sku,
                "item_id": item.id if item else None,
                "item_name": item.name if item else None,
                "unit": row.unit,
                "expected_quantity": expected,
                "counted_quantity": counted,
                "delta_quantity": delta,
                "remark": row.remark,
                "errors": errors,
            }
        )
    return {
        "branch_id": branch_id,
        "rows": payload,
        "valid": not any(row["errors"] for row in payload),
    }


async def apply_stock_audit(
    db: AsyncSession, *, data, user: User
) -> tuple[dict, InventoryTransaction]:
    preview = await preview_stock_audit(db, branch_id=data.branch_id, rows=data.rows)
    if not preview["valid"]:
        raise ConflictError("Stock audit contains invalid rows")
    await source_event_service.lock_branch_inventory(db, data.branch_id)
    existing = (
        await db.execute(
            select(InventoryTransaction).where(
                InventoryTransaction.idempotency_key
                == f"stock-audit:{data.idempotency_key}"
            )
        )
    ).scalar_one_or_none()
    if existing:
        preview["transaction_id"] = existing.id
        return preview, existing
    # Re-preview under the final lock: each delta is calculated against the
    # current ledger projection, never the quantity shown when a file was made.
    preview = await preview_stock_audit(db, branch_id=data.branch_id, rows=data.rows)
    branch = await db.get(Branch, data.branch_id)
    warehouse = await inventory_service.default_warehouse(db, data.branch_id)
    settings = (
        await db.execute(
            select(BranchInventorySettings).where(
                BranchInventorySettings.branch_id == data.branch_id
            )
        )
    ).scalar_one_or_none()
    is_opening_count = settings is not None and settings.go_live_at is None
    movement_type = (
        InventoryTransactionTypeEnum.OPENING_BALANCE.value
        if is_opening_count
        else InventoryTransactionTypeEnum.INVENTORY_COUNT.value
    )
    transaction = InventoryTransaction(
        reference=await inventory_service.next_reference(db, movement_type),
        type=movement_type,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=data.branch_id,
        warehouse_id=warehouse.id,
        business_date=await business_day_service.current_business_date(db, branch),
        creator_id=user.id,
        source_type="bulk_stock_audit",
        source_id=data.idempotency_key,
        idempotency_key=f"stock-audit:{data.idempotency_key}",
        items=[],
    )
    db.add(transaction)
    await db.flush()
    by_sku = {row.sku.casefold(): row for row in data.rows}
    for result in preview["rows"]:
        input_row = by_sku[result["sku"].casefold()]
        item = await db.get(InventoryItem, result["item_id"])
        transaction.items.append(
            InventoryTransactionItem(
                item_id=item.id,
                quantity=(
                    result["delta_quantity"]
                    if is_opening_count
                    else input_row.counted_quantity
                ),
                unit="ingredient" if is_opening_count else input_row.unit,
                conversion_factor=(
                    item.storage_to_ingredient_factor
                    if input_row.unit == "storage" and not is_opening_count
                    else Decimal("1")
                ),
                unit_cost=item.cost,
                notes=input_row.remark,
            )
        )
    await db.flush()
    posted = await inventory_service.post_transaction(
        db, transaction=transaction, user=user
    )
    if is_opening_count and settings is not None:
        settings.go_live_sequence = posted.posting_sequence
        settings.go_live_at = utcnow()
    preview["transaction_id"] = posted.id
    return preview, posted
