"""One-off: recost stranded transferred-in stock to its source branch's cost.

A receiving branch that does not produce (no local ingredients) holds finished
goods only via transfers. Before the transfer-cost fix, a transfer from a source
whose layers were 0-cost carried 0 to the destination, so the destination's stock
sits at average_cost 0 with no way to self-heal: it stocks no ingredients, so a
recipe recompute prices to 0 there, and the recost sweep therefore skips it (by
design — we never cost a branch from another branch's ingredient prices).

Going forward the transfer-cost fix handles this: once a source branch is costed,
its next transfer carries a real cost to the destination. This script only clears
the **existing** backlog: for each destination level still at 0, it finds the
source branch of the transfer that delivered that item there and, if that source
now has a cost, restates the destination to it — exactly the figure a fresh
transfer would carry. It uses the standard cost-adjustment path (a COST_ADJUSTMENT
that rescales the FIFO layers), so the change is on the ledger, not a bare column
write. A lot whose source is itself uncosted is left untouched (nothing to anchor
to yet — it heals when the source is costed and re-transfers).

    python scripts/recost_stranded_transfer_stock.py            # dry run
    python scripts/recost_stranded_transfer_stock.py --apply    # commit
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.branch import Branch  # noqa: E402
from app.models.inventory import (  # noqa: E402
    InventoryItem,
    InventoryLevel,
    Warehouse,
)
from app.models.operations import Transfer, TransferLine  # noqa: E402
from app.services.inventory import inventory_service  # noqa: E402


async def _source_cost(db, item_id, dst_branch_id) -> tuple[Decimal, str] | None:
    """The current cost of ``item_id`` at the source branch of the latest transfer
    that delivered it to ``dst_branch_id``. None when there is no such transfer or
    the source is itself uncosted."""
    transfer = (
        (
            await db.execute(
                select(Transfer)
                .join(TransferLine, TransferLine.transfer_id == Transfer.id)
                .where(
                    Transfer.branch_id == dst_branch_id,
                    TransferLine.item_id == item_id,
                    Transfer.received_transaction_id.is_not(None),
                )
                .order_by(Transfer.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if transfer is None:
        return None
    src_wh_id = transfer.source_warehouse_id
    if src_wh_id is None:
        src_wh = await inventory_service.default_warehouse(
            db, transfer.source_branch_id
        )
        src_wh_id = src_wh.id
    level = (
        (
            await db.execute(
                select(InventoryLevel).where(
                    InventoryLevel.item_id == item_id,
                    InventoryLevel.warehouse_id == src_wh_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if level is None:
        return None
    cost = Decimal(str(level.average_cost or 0))
    if cost <= 0:
        return None
    src_branch = await db.get(Branch, transfer.source_branch_id)
    return cost, (src_branch.name if src_branch else str(transfer.source_branch_id))


async def run(db, *, apply: bool) -> None:
    rows = (
        await db.execute(
            select(InventoryLevel, Warehouse, Branch, InventoryItem)
            .join(Warehouse, Warehouse.id == InventoryLevel.warehouse_id)
            .join(Branch, Branch.id == Warehouse.branch_id)
            .join(InventoryItem, InventoryItem.id == InventoryLevel.item_id)
            .where(
                InventoryLevel.quantity > 0,
                InventoryLevel.average_cost == 0,
                InventoryItem.kind == "produced_good",
                Warehouse.deleted_at.is_(None),
                Warehouse.is_active.is_(True),
            )
        )
    ).all()

    recost = 0
    skipped = 0
    total_value = Decimal("0")
    print(
        f"{'branch':<22}{'item':<38}{'qty':>8}{'src':>18}{'new cost':>14}{'Δvalue':>12}"
    )
    print("-" * 112)
    for level, warehouse, branch, item in rows:
        found = await _source_cost(db, item.id, branch.id)
        if found is None:
            skipped += 1
            continue
        cost, src_name = found
        qty = Decimal(str(level.quantity))
        delta = cost * qty
        total_value += delta
        recost += 1
        print(
            f"{branch.name[:21]:<22}{item.name[:37]:<38}{qty:>8}"
            f"{src_name[:17]:>18}{cost:>14}{delta:>12.4f}"
        )
        if apply:
            await inventory_service.adjust_cost(
                db,
                branch=branch,
                item_id=item.id,
                warehouse_id=warehouse.id,
                new_average_cost=cost,
                user=None,
                notes="One-off recost: stranded transfer-in stock to source cost",
            )

    print("-" * 112)
    print(
        f"{'RECOST' if apply else 'WOULD RECOST'}: {recost} level(s), "
        f"+{total_value:.4f} on the books.  Skipped (no costed source): {skipped}."
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--apply", action="store_true", help="commit (default dry-run)")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("set DATABASE_URL or pass --database-url")

    engine = create_async_engine(args.database_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        await run(db, apply=args.apply)
        if args.apply:
            await db.commit()
            print("committed")
        else:
            await db.rollback()
            print("dry run — rolled back")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
