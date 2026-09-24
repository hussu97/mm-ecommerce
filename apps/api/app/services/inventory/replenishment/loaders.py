"""Assemble the engine's snapshot as of a moment.

Point-in-time on purpose: stock is summed from ledger lines *posted* by `as_of`
(the ledger and `inventory_levels` agree exactly, so "now" reads the same as the
levels screen), and only facts for business days before the snapshot's day are
read. A backtest at 09:00 on a past day therefore sees what the form would have
seen then — except that the menu, recipes and floors are today's.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryTransaction,
    InventoryTransactionItem,
    TransactionStatusEnum,
)
from app.models.inventory_v2 import BranchInventorySettings, InventoryItemKindEnum
from app.models.replenishment import ReplenishmentDailyFact
from app.services.inventory import auto_availability_service, recipe_service
from app.services.inventory.replenishment import engine
from app.services.inventory.replenishment.facts import branch_calendars
from app.services.inventory.replenishment.settings import load_settings, to_engine
from app.services.pos import business_day_service


async def load_facts(db: AsyncSession, start: date, end: date) -> list[engine.Fact]:
    """Facts for business days in [start, end)."""
    rows = (
        (
            await db.execute(
                select(ReplenishmentDailyFact).where(
                    ReplenishmentDailyFact.business_date >= start,
                    ReplenishmentDailyFact.business_date < end,
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        engine.Fact(
            branch_id=row.branch_id,
            item_id=row.item_id,
            business_date=row.business_date,
            sales_units=float(row.sales_units),
            hourly_units=tuple(float(u) for u in row.hourly_units),
            hourly_open_minutes=tuple(row.hourly_open_minutes),
            hourly_in_stock_minutes=(
                tuple(row.hourly_in_stock_minutes)
                if row.hourly_in_stock_minutes is not None
                else None
            ),
        )
        for row in rows
    ]


async def on_hand_as_of(
    db: AsyncSession, item_ids: list[uuid.UUID], as_of: datetime
) -> dict[tuple[uuid.UUID, uuid.UUID], float]:
    rows = (
        await db.execute(
            select(
                InventoryTransaction.branch_id,
                InventoryTransactionItem.item_id,
                func.sum(InventoryTransactionItem.signed_quantity),
            )
            .join(
                InventoryTransaction,
                InventoryTransaction.id == InventoryTransactionItem.transaction_id,
            )
            .where(
                InventoryTransaction.status == TransactionStatusEnum.CLOSED.value,
                InventoryTransaction.posted_at <= as_of,
                InventoryTransactionItem.item_id.in_(item_ids),
            )
            .group_by(InventoryTransaction.branch_id, InventoryTransactionItem.item_id)
        )
    ).all()
    return {
        (branch_id, item_id): float(total or 0) for branch_id, item_id, total in rows
    }


async def floors(
    db: AsyncSession, branch_ids: list[uuid.UUID], item_ids: set[uuid.UUID]
) -> tuple[
    dict[tuple[uuid.UUID, uuid.UUID], float], dict[tuple[uuid.UUID, uuid.UUID], float]
]:
    """(branch, item) → the most, and the least, one sale of anything sold at the
    branch draws of the item."""
    catalog = await recipe_service.load_active_catalog(db)
    requirements = auto_availability_service.requirements_by_owner(catalog)
    most: dict[tuple[uuid.UUID, uuid.UUID], float] = defaultdict(float)
    least: dict[tuple[uuid.UUID, uuid.UUID], float] = {}
    for branch_id in branch_ids:
        for owner in await auto_availability_service.owners_sold_at(db, branch_id):
            for item_id, per_sale in requirements.get(owner, {}).items():
                draw = float(per_sale)
                if item_id not in item_ids or draw <= 0:
                    continue
                key = (branch_id, item_id)
                most[key] = max(most[key], draw)
                least[key] = min(least.get(key, draw), draw)
    return dict(most), least


async def build_snapshot(
    db: AsyncSession,
    *,
    as_of: datetime,
    source_branch_id: uuid.UUID | None = None,
) -> engine.Snapshot:
    settings_row = await load_settings(db)
    pool_id = source_branch_id or settings_row.production_branch_id
    if pool_id is None:
        raise BadRequestError(
            "Choose a source branch, or set the production branch in the "
            "replenishment settings"
        )
    branches = list(
        (
            await db.execute(
                select(Branch)
                .where(Branch.deleted_at.is_(None), Branch.is_active.is_(True))
                .order_by(Branch.display_order, Branch.name)
            )
        )
        .scalars()
        .all()
    )
    pool = next((b for b in branches if b.id == pool_id), None)
    if pool is None:
        raise BadRequestError("The source branch is not an active branch")

    tz = await business_day_service.resolve_timezone(db)
    business_date = date.fromisoformat(
        business_day_service.business_date_for(pool, as_of, tz)
    )
    settings = to_engine(settings_row)

    cals = await branch_calendars(db, branches)
    items_rows = (
        (
            await db.execute(
                select(InventoryItem).where(
                    InventoryItem.kind == InventoryItemKindEnum.PRODUCED_GOOD.value,
                    InventoryItem.deleted_at.is_(None),
                    InventoryItem.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    producible = await recipe_service.producible_item_ids(db)
    bases = await recipe_service.producible_item_bases(db)
    items = {
        item.id: engine.ItemInfo(
            id=item.id,
            name=item.name,
            category_id=item.category_id,
            storage_unit=item.storage_unit,
            producible=item.id in producible,
            basis=bases.get(item.id, ("unit", None))[0],
            batch_yield=(
                float(bases[item.id][1])
                if item.id in bases and bases[item.id][1]
                else None
            ),
            shelf_life_days=item.shelf_life_days or 14,
        )
        for item in items_rows
    }
    item_ids = list(items)
    pool_produces = bool(
        await db.scalar(
            select(BranchInventorySettings.production_enabled).where(
                BranchInventorySettings.branch_id == pool_id
            )
        )
    )
    most, least = await floors(db, [b.id for b in branches], set(item_ids))
    return engine.Snapshot(
        business_date=business_date,
        now=as_of.astimezone(tz).replace(tzinfo=None),
        pool_branch_id=pool_id,
        branches={
            branch.id: engine.BranchInfo(branch.id, branch.name, cals[branch.id])
            for branch in branches
        },
        items=items,
        facts=await load_facts(
            db, business_date - timedelta(days=settings.window_days), business_date
        ),
        on_hand=await on_hand_as_of(db, item_ids, as_of),
        floors=most,
        min_floors=least,
        settings=settings,
        pool_produces=pool_produces,
    )
