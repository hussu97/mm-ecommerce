"""Immutable-ledger corrections and deterministic level projection rebuilds."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.money import quantity, unit_cost
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.inventory import (
    InventoryCategory,
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    PurchaseOrder,
    PurchaseOrderStatusEnum,
    TransactionStatusEnum,
    Warehouse,
)
from app.models.inventory_v2 import BranchInventorySettings
from app.models.user import User
from app.services.inventory import (
    costing_service,
    inventory_service,
    source_event_service,
)
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
    """
    Replay the FIFO costing engine over the whole ledger and report/repair drift.

    Costing is a projection of the immutable ledger (`costing_engine`), and a
    price learned in one branch can re-cost stock transferred to another, so the
    replay is always estate-wide, under every branch's lock. The drift reported
    is this branch's: every level whose stored quantity or average differs from
    what the ledger implies. A dry run computes the same replay and writes
    nothing, so the preview and the apply can never use different arithmetic.
    """
    branch = await db.get(Branch, branch_id)
    if branch is None:
        raise NotFoundError("Branch not found")
    warehouse = await inventory_service.default_warehouse(db, branch_id)
    warehouse_ids = set(
        (await db.execute(select(Warehouse.id).where(Warehouse.branch_id == branch_id)))
        .scalars()
        .all()
    ) or {warehouse.id}

    levels = {
        (level.item_id, level.warehouse_id): level
        for level in (
            await db.execute(
                select(InventoryLevel).where(
                    InventoryLevel.warehouse_id.in_(warehouse_ids)
                )
            )
        )
        .scalars()
        .all()
    }
    cached = {
        key: (
            Decimal(str(level.quantity or 0)),
            Decimal(str(level.average_cost or 0)),
        )
        for key, level in levels.items()
    }

    result = await costing_service.replay_estate(db, wait=True, write=apply)
    projected = {
        key: out
        for key, out in result.projection.levels.items()
        if key[1] in warehouse_ids
    }

    drifts: list[ProjectionDrift] = []
    for key in sorted(
        set(cached) | set(projected), key=lambda k: (str(k[1]), str(k[0]))
    ):
        cached_quantity, cached_average = cached.get(key, (Decimal("0"), Decimal("0")))
        out = projected.get(key)
        ledger_quantity = quantity(out.quantity if out else 0)
        ledger_average = unit_cost(out.average_cost if out else 0)
        if cached_quantity != ledger_quantity or cached_average != ledger_average:
            drifts.append(
                ProjectionDrift(
                    item_id=key[0],
                    warehouse_id=key[1],
                    cached_quantity=cached_quantity,
                    ledger_quantity=ledger_quantity,
                    cached_average_cost=cached_average,
                    ledger_average_cost=ledger_average,
                    through_sequence=out.through_sequence if out else None,
                )
            )
    if apply and drifts:
        # Auto off-sale (experimental): a corrected level can cross zero, so the
        # produced goods it corrected are re-evaluated (no-op unless enabled).
        from app.services.inventory import auto_availability_service

        await auto_availability_service.mark_items_at_enabled_branches(
            db, item_ids={drift.item_id for drift in drifts}, branch_id=branch_id
        )
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
    branch = await db.get(Branch, original.branch_id)
    if branch is None:
        raise NotFoundError("Branch not found")
    # Serialize before the idempotency lookup. Otherwise two requests can both
    # observe no reversal and the loser fails on the unique key after the first
    # has already changed stock.
    await source_event_service.lock_branch_inventory(db, original.branch_id)
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
    correction_group = original.correction_group_id or uuid.uuid4()
    reversal_type = (
        InventoryTransactionTypeEnum.COST_ADJUSTMENT.value
        if original.type == InventoryTransactionTypeEnum.COST_ADJUSTMENT.value
        else InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value
    )
    reversal = InventoryTransaction(
        reference=await inventory_service.next_reference(db, reversal_type),
        type=reversal_type,
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
                # Link to the line being undone so the FIFO engine restores (or
                # removes) exactly the layers the original movement touched.
                reverses_line_id=line.id,
                # signed_quantity is in storage units, so the reversal is a
                # storage movement; keep the original line's factor snapshot.
                quantity=quantity(-Decimal(str(line.signed_quantity))),
                unit="storage",
                conversion_factor=Decimal(str(line.conversion_factor or 1)),
                unit_cost=(
                    unit_cost(line.previous_unit_cost)
                    if original.type
                    == InventoryTransactionTypeEnum.COST_ADJUSTMENT.value
                    and line.previous_unit_cost is not None
                    else inventory_service.line_cost_in_storage_unit(line)
                ),
                recipe_version_id=line.recipe_version_id,
                recipe_path=line.recipe_path or [],
                notes=f"Reversal of {original.reference}",
            )
        )
    await db.flush()
    return await inventory_service.post_transaction(db, transaction=reversal, user=user)


async def void_purchase_order(
    db: AsyncSession, *, purchase_order_id: uuid.UUID, user: User, reason: str
) -> PurchaseOrder:
    """
    Void a purchase order: reverse any stock it received, then cancel it.

    A received PO (``closed``/``partially_received``) has one or more posted
    ``purchasing`` transactions linked by ``purchase_order_id``. Each is reversed
    through :func:`reverse_transaction`, which redraws the FIFO layers and
    restates the item's weighted-average cost — booking a shortfall for anything
    already consumed — so both the quantity and the costing are undone. An
    un-received PO has no such transaction, so voiding it only moves the status.

    The move itself is validated and audit-stamped by
    ``inventory_service.transition_purchase_order`` (raising if the order is
    already terminal). Reversal is idempotent, so a retried void is safe. The
    caller is responsible for recomputing the VAT window afterwards, so a PO
    whose input VAT was already on the reclaim ledger drops off it.
    """
    purchase_order = (
        await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == purchase_order_id)
        )
    ).scalar_one_or_none()
    if purchase_order is None:
        raise NotFoundError("Purchase order not found")

    # Refuse early with the state-machine's message if this order can't be voided
    # (already declined/voided), before we touch any stock.
    inventory_service.assert_can_transition_purchase_order(
        purchase_order, PurchaseOrderStatusEnum.VOIDED, user=user
    )

    posted_receipts = (
        (
            await db.execute(
                select(InventoryTransaction).where(
                    InventoryTransaction.purchase_order_id == purchase_order_id,
                    InventoryTransaction.type
                    == InventoryTransactionTypeEnum.PURCHASING.value,
                    InventoryTransaction.status == TransactionStatusEnum.CLOSED.value,
                )
            )
        )
        .scalars()
        .all()
    )
    for receipt in posted_receipts:
        await reverse_transaction(
            db, transaction_id=receipt.id, user=user, reason=reason
        )

    await inventory_service.transition_purchase_order(
        db, purchase_order, PurchaseOrderStatusEnum.VOIDED, user=user
    )
    await db.flush()
    return purchase_order


async def preview_stock_audit(db: AsyncSession, *, branch_id: uuid.UUID, rows) -> dict:
    branch = await db.get(Branch, branch_id)
    if branch is None:
        raise NotFoundError("Branch not found")
    warehouse = await inventory_service.default_warehouse(db, branch_id)
    sku_keys = {row.sku.strip().casefold() for row in rows if row.sku.strip()}
    matching_items = list(
        (
            await db.execute(
                select(InventoryItem).where(
                    func.lower(InventoryItem.sku).in_(sku_keys),
                    InventoryItem.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    items_by_sku: dict[str, list[InventoryItem]] = {}
    for inventory_item in matching_items:
        items_by_sku.setdefault(inventory_item.sku.casefold(), []).append(
            inventory_item
        )
    # Resolve categories in one query (never lazily off item.category under asyncio)
    # so the preview can be grouped by category like every other line table.
    category_ids = {
        inventory_item.category_id
        for inventory_item in matching_items
        if inventory_item.category_id is not None
    }
    categories_by_id: dict[uuid.UUID, InventoryCategory] = {}
    if category_ids:
        categories_by_id = {
            category.id: category
            for category in (
                await db.execute(
                    select(InventoryCategory).where(
                        InventoryCategory.id.in_(category_ids)
                    )
                )
            )
            .scalars()
            .all()
        }
    levels_by_item = {
        level.item_id: level
        for level in (
            await db.execute(
                select(InventoryLevel).where(
                    InventoryLevel.warehouse_id == warehouse.id,
                    InventoryLevel.item_id.in_(
                        [inventory_item.id for inventory_item in matching_items]
                    ),
                )
            )
        )
        .scalars()
        .all()
    }
    seen: set[str] = set()
    payload = []
    for row in rows:
        errors: list[str] = []
        sku_key = row.sku.strip().casefold()
        if sku_key in seen:
            errors.append("Duplicate SKU")
        seen.add(sku_key)
        items = items_by_sku.get(sku_key, [])
        item = items[0] if len(items) == 1 else None
        if not items:
            errors.append("Unknown SKU")
        elif len(items) > 1:
            errors.append("Ambiguous SKU")
        expected = None
        counted = quantity(row.counted_quantity)
        delta = None
        normalised_delta = None
        if Decimal(str(row.counted_quantity)) != counted:
            errors.append("Quantity has more than four decimal places")
        if item:
            # The level is held in storage units (the canonical stock unit).
            storage_expected = quantity(
                levels_by_item[item.id].quantity if item.id in levels_by_item else 0
            )
            factor = Decimal(str(item.storage_to_ingredient_factor))
            if row.unit == "storage":
                # Counting in storage: what's shown and the canonical figure match.
                expected = storage_expected
                normalised = counted
            else:
                # Counting in ingredient units: show the expected in ingredient
                # units (storage × factor) and convert the count back to storage.
                expected = quantity(storage_expected * factor)
                normalised = quantity(counted / factor)
            delta = quantity(counted - expected)
            normalised_delta = quantity(normalised - storage_expected)
            if abs(delta) > max(abs(expected) * Decimal("10"), Decimal("100000")):
                errors.append("Extreme variance requires manual review")
        category = (
            categories_by_id.get(item.category_id)
            if item and item.category_id
            else None
        )
        payload.append(
            {
                "sku": row.sku,
                "item_id": item.id if item else None,
                "item_name": item.name if item else None,
                "category_name": category.name if category else None,
                "category_order": (
                    int(category.display_order or 0) if category else None
                ),
                "unit": row.unit,
                "expected_quantity": expected,
                "counted_quantity": counted,
                "delta_quantity": delta,
                "normalised_delta_quantity": normalised_delta,
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
    if settings is None:
        settings = BranchInventorySettings(branch_id=data.branch_id)
        db.add(settings)
        await db.flush()
    is_opening_count = settings.go_live_at is None
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
        level = await inventory_service.level_for(db, item.id, warehouse.id)
        canonical_cost = unit_cost(level.average_cost)
        if is_opening_count or canonical_cost == 0:
            canonical_cost = inventory_service.inventory_item_cost_for_unit(
                item, "storage"
            )
        # Both an opening balance and a count now SET the level (see
        # inventory_service.post_transaction), so both post the *counted* quantity
        # in the row's own unit and let the poster net it against the current
        # level. Opening no longer pre-computes a delta — that only worked while
        # OPENING_BALANCE added, and doubled the balance on any re-run.
        entry_unit = input_row.unit
        transaction.items.append(
            InventoryTransactionItem(
                item_id=item.id,
                quantity=input_row.counted_quantity,
                unit=entry_unit,
                # The item's real factor either way, so post_transaction records
                # both the storage and ingredient views of the count.
                conversion_factor=item.storage_to_ingredient_factor,
                unit_cost=inventory_service.canonical_cost_for_unit(
                    item, canonical_cost, entry_unit
                ),
                notes=input_row.remark,
            )
        )
    await db.flush()
    posted = await inventory_service.post_transaction(
        db, transaction=transaction, user=user
    )
    if is_opening_count:
        settings.go_live_sequence = posted.posting_sequence
        settings.go_live_at = utcnow()
    preview["transaction_id"] = posted.id
    return preview, posted
