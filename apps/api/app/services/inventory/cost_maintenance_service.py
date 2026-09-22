"""Self-healing recost of zero-cost made stock.

Costing is FIFO: a made item is worth its recipe (the FIFO cost of its
ingredients), but stock can still enter the ledger at **zero** cost — an opening
balance keyed with no price, a shift-report receipt, a count overage before the
item has ever been costed, a transfer drawn from a zero-cost source. Nothing in
the forward path later revalues those layers once a real cost exists; only a
manual rebuild or the admin "reset cost from recipe" action did (costing audit
G3/G5).

This is the missing trigger point: a background sweep that finds made items
holding stock valued at zero and, when their recipe now has a cost, restates that
stock to it — through the ordinary ``adjust_cost`` path (a COST_ADJUSTMENT that
rescales the FIFO layers), attributed to the system. It is idempotent: once a
level is revalued its average is non-zero, so the next tick passes it by, and an
item whose ingredients are themselves still uncosted (recipe cost 0) is left
untouched until there is a real figure to anchor to.

Same lifespan shape as its neighbours: no cron in this stack, an advisory lock so
a second copy across blue/green is harmless, storefront app only.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import advisory_lock, heartbeat
from app.core.exceptions import AppError
from app.core.money import unit_cost as _c
from app.models.branch import Branch
from app.models.inventory import InventoryItem, InventoryLevel, Warehouse
from app.models.inventory_v2 import (
    Recipe,
    RecipeOwnerKindEnum,
    RecipeVersion,
    RecipeVersionStatusEnum,
)
from app.services.inventory import inventory_service, recipe_service

logger = logging.getLogger(__name__)

#: Same flat 64-bit namespace as every other advisory lock. "mmBATCH" + 0D, the
#: next free value after the settled-order sweeper (0C).
_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_480D

#: Every six hours. Recipe costs move at the pace of purchase prices, not orders,
#: and the sweep only ever touches stock still sitting at zero, so a slow cadence
#: heals it well within a trading day without churning cost adjustments.
_TICK_SECONDS = 6 * 60 * 60


async def sweep_zero_cost_recipe_stock(
    db: AsyncSession, *, now: datetime | None = None
) -> list[dict]:
    """Revalue every zero-cost on-hand level of a made item to its recipe cost.

    A candidate is an ``InventoryLevel`` with quantity on hand and an average cost
    of zero, whose item owns an **active** recipe. Each item's current recipe cost
    is computed once (estate-wide FIFO of its ingredients) and applied per level
    via ``adjust_cost``; an item whose recipe still prices to zero — its own
    ingredients uncosted — is skipped. The caller commits. Returns one record per
    level revalued.
    """
    _ = now  # candidates are chosen by stored cost, not by time
    active_recipe_item_ids = (
        select(Recipe.inventory_item_id)
        .join(RecipeVersion, RecipeVersion.recipe_id == Recipe.id)
        .where(
            Recipe.owner_kind == RecipeOwnerKindEnum.INVENTORY_ITEM.value,
            Recipe.inventory_item_id.is_not(None),
            RecipeVersion.status == RecipeVersionStatusEnum.ACTIVE.value,
        )
    )
    rows = (
        await db.execute(
            select(InventoryLevel, Warehouse, Branch, InventoryItem)
            .join(Warehouse, Warehouse.id == InventoryLevel.warehouse_id)
            .join(Branch, Branch.id == Warehouse.branch_id)
            .join(InventoryItem, InventoryItem.id == InventoryLevel.item_id)
            .where(
                InventoryLevel.quantity > 0,
                InventoryLevel.average_cost == 0,
                # Skip warehouses that can no longer be posted to — adjust_cost
                # rejects them, and one such level must not poison the sweep.
                Warehouse.deleted_at.is_(None),
                Warehouse.is_active.is_(True),
                InventoryLevel.item_id.in_(active_recipe_item_ids),
            )
        )
    ).all()
    if not rows:
        return []

    # One recipe graph for the whole sweep (expansion is reused); the cost itself
    # is computed per level against that branch's own ingredient levels, so a
    # branch is never revalued using another branch's ingredient prices.
    catalog = await recipe_service.load_active_catalog(db)

    fixed: list[dict] = []
    for level, warehouse, branch, item in rows:
        per_ingredient = await recipe_service.recipe_unit_cost(
            db, item_id=item.id, warehouse_id=warehouse.id, catalog=catalog
        )
        if not per_ingredient or per_ingredient <= 0:
            # Ingredients still uncosted at this branch — nothing to anchor to yet.
            continue
        factor = Decimal(str(item.storage_to_ingredient_factor or 1))
        storage_cost = _c(per_ingredient * factor)
        # Savepoint each level so one that cannot be posted (a race, a guard) is
        # skipped without rolling back the levels already healed this tick.
        try:
            async with db.begin_nested():
                result = await inventory_service.adjust_cost(
                    db,
                    branch=branch,
                    item_id=item.id,
                    warehouse_id=warehouse.id,
                    new_average_cost=storage_cost,
                    user=None,
                    notes="Auto-recost from recipe (zero-cost stock)",
                )
            fixed.append(
                {
                    "item_id": str(item.id),
                    "item_name": item.name,
                    "branch": branch.name,
                    "new_average_cost": storage_cost,
                    "value_change": result["value_change"],
                }
            )
        except AppError as exc:
            logger.info(
                "Recost sweep: skipped %s @ %s — %s", item.name, branch.name, exc
            )
    return fixed


async def run_forever() -> None:
    """Heal zero-cost made stock on a leader-elected loop.

    Leader-elected on an advisory lock and beating its heartbeat, the same shape
    as the other lifespan loops — no cron in this stack, one worker inside a sweep
    at a time.
    """
    logger.info("Zero-cost recost sweeper started (every %ss)", _TICK_SECONDS)
    while True:
        try:
            # Sleeps first: boot is busy and nothing here is urgent.
            await asyncio.sleep(_TICK_SECONDS)
            await heartbeat.beat("cost_recost_sweeper")
            async with advisory_lock.held_session(
                _ADVISORY_LOCK_KEY, name="cost recost sweeper"
            ) as db:
                if db is None:
                    continue
                fixed = await sweep_zero_cost_recipe_stock(db)
                await db.commit()
                if fixed:
                    logger.info(
                        "Recost sweeper revalued %s zero-cost level(s): %s",
                        len(fixed),
                        [
                            f"{f['item_name']}@{f['branch']}={f['new_average_cost']}"
                            for f in fixed
                        ],
                    )
        except asyncio.CancelledError:
            logger.info("Recost sweeper stopping")
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
            logger.exception("Recost sweeper tick failed")
