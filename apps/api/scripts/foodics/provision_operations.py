"""
Set up the operational data a branch needs before it can trade.

The catalogue importer covers everything Foodics exports. This covers what it
does not: a warehouse to hold stock, opening stock levels, the floor plan, and
a food cost against each product. Foodics keeps all of it per-branch behind
screens its export API never exposes, so it has to be established here.

    python scripts/foodics/provision_operations.py [--dry-run]
    python scripts/foodics/provision_operations.py --tables 8 --food-cost 0.32

Idempotent: re-running updates in place and never overwrites a figure an
operator has since corrected in the console.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from app.models import (  # noqa: E402
    Branch,
    InventoryCategory,
    InventoryItem,
    InventoryLevel,
    PosTable,
    Product,
    Section,
    Warehouse,
)

#: Foodics groups stock items under a handful of standing categories. These are
#: the ones a bakery and coffee counter actually buys against.
INVENTORY_CATEGORIES = [
    "Dairy",
    "Bakery & Flour",
    "Chocolate & Confectionery",
    "Fruit & Nuts",
    "Coffee & Tea",
    "Beverages",
    "Packaging & Disposables",
    "Cleaning & Consumables",
]

#: Matched against the stock item's name, first hit wins. Anything unmatched is
#: left uncategorised rather than forced into a bucket it does not belong in —
#: a wrong category quietly corrupts every stock report that groups by it.
CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("Dairy", ("milk", "cream", "butter", "cheese", "yogurt", "labneh", "mascarpone")),
    ("Bakery & Flour", ("flour", "sugar", "yeast", "baking", "starch", "semolina")),
    (
        "Chocolate & Confectionery",
        ("chocolate", "cocoa", "ganache", "fondant", "candy"),
    ),
    (
        "Fruit & Nuts",
        (
            "berry",
            "berries",
            "mango",
            "banana",
            "pistachio",
            "almond",
            "walnut",
            "hazelnut",
            "cashew",
            "date",
            "lemon",
            "orange",
        ),
    ),
    ("Coffee & Tea", ("coffee", "espresso", "bean", "tea", "matcha", "chai")),
    ("Beverages", ("juice", "water", "soda", "syrup", "cola", "sparkling")),
    (
        "Packaging & Disposables",
        (
            "box",
            "cup",
            "lid",
            "bag",
            "napkin",
            "straw",
            "sleeve",
            "tray",
            "container",
            "wrap",
        ),
    ),
    ("Cleaning & Consumables", ("clean", "sanitis", "sanitiz", "detergent", "glove")),
]


def categorise(name: str) -> str | None:
    lowered = name.lower()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return category
    return None


class Provisioner:
    def __init__(self, db, args):
        self.db = db
        self.args = args
        self.stats: dict[str, int] = {}

    def record(self, entity: str, n: int = 1) -> None:
        self.stats[entity] = self.stats.get(entity, 0) + n

    async def one(self, model, **where):
        return (
            await self.db.execute(select(model).filter_by(**where))
        ).scalar_one_or_none()

    async def all(self, model, **where):
        stmt = select(model).filter_by(**where) if where else select(model)
        return list((await self.db.execute(stmt)).scalars())

    # ── Inventory ────────────────────────────────────────────────────────────

    async def inventory_categories(self) -> dict[str, InventoryCategory]:
        by_name: dict[str, InventoryCategory] = {}
        for order, name in enumerate(INVENTORY_CATEGORIES):
            category = await self.one(InventoryCategory, name=name)
            if category is None:
                category = InventoryCategory(
                    name=name, display_order=order, is_active=True
                )
                self.db.add(category)
                self.record("inventory category")
            by_name[name] = category
        await self.db.flush()
        return by_name

    async def classify_items(self, categories: dict[str, InventoryCategory]) -> None:
        for item in await self.all(InventoryItem):
            if item.category_id is not None:
                continue  # already sorted, by us or by a person
            name = categorise(item.name)
            if name:
                item.category_id = categories[name].id
                self.record("item categorised")
        await self.db.flush()

    async def warehouses(self) -> list[Warehouse]:
        """One default warehouse per branch — the stock room behind the counter."""
        made = []
        for branch in await self.all(Branch):
            warehouse = await self.one(Warehouse, branch_id=branch.id, is_default=True)
            if warehouse is None:
                warehouse = Warehouse(
                    branch_id=branch.id,
                    name=f"{branch.name} Store",
                    reference=f"{branch.reference or 'WH'}-STORE",
                    is_default=True,
                    is_active=True,
                )
                self.db.add(warehouse)
                self.record("warehouse")
            made.append(warehouse)
        await self.db.flush()
        return made

    async def stock_levels(self, warehouses: list[Warehouse]) -> None:
        """
        A level row per item per warehouse, opening at zero.

        Zero rather than a made-up opening quantity: the first stock count is
        what establishes real numbers, and an invented figure would show as
        sellable stock nobody can find on a shelf. What matters is that the row
        exists, so a receipt or a count has somewhere to land.
        """
        items = await self.all(InventoryItem)
        for warehouse in warehouses:
            for item in items:
                if await self.one(
                    InventoryLevel, item_id=item.id, warehouse_id=warehouse.id
                ):
                    continue
                self.db.add(
                    InventoryLevel(
                        item_id=item.id,
                        warehouse_id=warehouse.id,
                        quantity=Decimal("0"),
                        average_cost=item.cost or Decimal("0"),
                    )
                )
                self.record("stock level")
            await self.db.flush()

    # ── Floor plan ───────────────────────────────────────────────────────────

    async def floor_plan(self) -> None:
        """
        A section and its tables per branch.

        Without one there is no dine-in at all: an order cannot be seated, so
        split, join and move-table are unreachable however well they work.
        """
        for branch in await self.all(Branch):
            section = await self.one(Section, branch_id=branch.id, name="Main Floor")
            if section is None:
                section = Section(
                    branch_id=branch.id,
                    name="Main Floor",
                    name_localized="الصالة الرئيسية",
                    translations={"ar": {"name": "الصالة الرئيسية"}},
                    display_order=0,
                    is_active=True,
                )
                self.db.add(section)
                self.record("section")
                await self.db.flush()

            for number in range(1, self.args.tables + 1):
                name = f"T{number}"
                if await self.one(PosTable, section_id=section.id, name=name):
                    continue
                self.db.add(
                    PosTable(
                        section_id=section.id,
                        name=name,
                        seats=2 if number <= 2 else 4,
                        status="free",
                        accepts_reservations=branch.accepts_reservations,
                        display_order=number,
                        is_active=True,
                    )
                )
                self.record("table")
            await self.db.flush()

    # ── Costing ──────────────────────────────────────────────────────────────

    async def product_costs(self) -> None:
        """
        Give every product a cost so margin reports mean something.

        This is an estimate from a food-cost ratio, not a recipe costing — the
        export carries no cost at all, and a catalogue of zero-cost products
        reports every sale at 100% margin, which is worse than approximately
        right. Only ever written where cost is unset, so a real figure entered
        in the console or derived from a recipe is never overwritten.
        """
        ratio = Decimal(str(self.args.food_cost))
        for product in await self.all(Product):
            if product.cost is not None and product.cost > 0:
                continue
            if product.is_non_revenue:
                continue
            base = product.base_price or Decimal("0")
            if base <= 0:
                continue
            product.cost = (base * ratio).quantize(Decimal("0.01"))
            self.record("product costed")
        await self.db.flush()

    async def run(self) -> None:
        categories = await self.inventory_categories()
        await self.classify_items(categories)
        warehouses = await self.warehouses()
        await self.stock_levels(warehouses)
        await self.floor_plan()
        if self.args.food_cost > 0:
            await self.product_costs()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--tables", type=int, default=8, help="tables per branch (default 8)"
    )
    parser.add_argument(
        "--food-cost",
        type=float,
        default=0.32,
        help="cost as a fraction of selling price, 0 to skip (default 0.32)",
    )
    parser.add_argument("--dry-run", action="store_true", help="roll back at the end")
    args = parser.parse_args()

    if not args.database_url:
        parser.error("set DATABASE_URL or pass --database-url")

    engine = create_async_engine(args.database_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        provisioner = Provisioner(db, args)
        await provisioner.run()
        if args.dry_run:
            await db.rollback()
        else:
            await db.commit()

    width = max((len(k) for k in provisioner.stats), default=0)
    for entity, count in sorted(provisioner.stats.items()):
        print(f"  {entity:<{width}}  {count:>5}")
    if args.dry_run:
        print("\ndry run — rolled back")
    elif args.food_cost > 0:
        print(
            f"\nProduct costs are estimated at {args.food_cost:.0%} of price. "
            "Replace them with recipe costing before trusting margin reports."
        )
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
