"""
What a product costs to make against what it sells for — for the console.

A sale draws the product's own recipe (unless it does not consume stock) plus
the recipe of each option chosen (`recipe_service.snapshot_order`), and is
priced at the base price plus the option's price. So:

* a product **without** options costs its own recipe, against its base price;
* a product **with** options costs, per option, its own recipe plus that
  option's recipe, against base price + option price.

Costs are the recipe at current FIFO cost, blended across every warehouse with
the last known cost standing in for an ingredient that has run out (so a
sold-out item is not priced at zero). Everything is computed here, including
the percentages (canon rule 10).

One request is a fixed handful of queries whatever the page size: the products
with their category, modifiers and options (selectin), the warehouse ids, the
recipe catalogue, and one ingredient-cost query. The catalogue export
(`catalogue_report`) is the same pass over every active product.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.money import money
from app.models.inventory import Warehouse
from app.models.inventory_v2 import RecipeOwnerKindEnum
from app.models.modifier import Modifier, ProductModifier
from app.models.product import Product
from app.services.inventory import recipe_service

_PRODUCT = RecipeOwnerKindEnum.PRODUCT.value
_OPTION = RecipeOwnerKindEnum.MODIFIER_OPTION.value


def cost_share(cost: Decimal | None, price: Decimal) -> Decimal | None:
    """Cost as a % of price; null when either is unknown or the price is 0."""
    if cost is None or price <= 0:
        return None
    return money(cost / price * 100)


@dataclass
class OptionCost:
    modifier_option_id: uuid.UUID
    modifier_name: str
    name: str
    #: Base price + the option's price: what the item sells for with it.
    price: Decimal
    #: The product's own recipe + the option's — what a sale of it draws. Null
    #: when the option has no active recipe.
    cost: Decimal | None
    cost_pct: Decimal | None


@dataclass
class ProductCost:
    product_id: uuid.UUID
    price: Decimal
    consumes_stock: bool
    #: The product's own recipe at one unit; null with no active recipe, and
    #: zero when the product consumes no stock (its options carry the cost).
    cost: Decimal | None
    cost_pct: Decimal | None
    #: It consumes stock but has no active recipe of its own: a sale warns
    #: `missing_recipe` and draws only its options' recipes.
    missing_recipe: bool
    options: list[OptionCost] = field(default_factory=list)


async def _load_products(
    db: AsyncSession, product_ids: list[uuid.UUID] | None
) -> list[Product]:
    """The products (all active ones when *product_ids* is None) with their
    category, modifiers and options — three selectin queries, not one per row."""
    stmt = select(Product).options(
        selectinload(Product.category),
        selectinload(Product.product_modifiers)
        .selectinload(ProductModifier.modifier)
        .selectinload(Modifier.options),
    )
    stmt = (
        stmt.where(Product.is_active.is_(True))
        if product_ids is None
        else stmt.where(Product.id.in_(product_ids))
    )
    return list((await db.execute(stmt)).scalars().unique().all())


def _option_links(product: Product):
    """(modifier, active options) per link, in display order."""
    for link in sorted(product.product_modifiers, key=lambda link: link.display_order):
        options = sorted(
            (o for o in link.modifier.options if o.is_active),
            key=lambda o: (o.display_order, o.name),
        )
        yield link.modifier, options


async def _price(
    db: AsyncSession, products: list[Product]
) -> recipe_service.PricedOwners:
    owners: set[tuple[str, uuid.UUID]] = set()
    for product in products:
        owners.add((_PRODUCT, product.id))
        for _, options in _option_links(product):
            owners.update((_OPTION, option.id) for option in options)
    warehouse_ids = list(
        (await db.execute(select(Warehouse.id).where(Warehouse.is_active))).scalars()
    )
    return await recipe_service.price_owners(db, owners, warehouse_ids=warehouse_ids)


def _cost_of(product: Product, priced: recipe_service.PricedOwners) -> ProductCost:
    base = Decimal(str(product.base_price or 0))
    own = (
        priced.unit_cost((_PRODUCT, product.id))
        if product.consumes_stock
        else Decimal(0)
    )
    entry = ProductCost(
        product_id=product.id,
        price=money(base),
        consumes_stock=product.consumes_stock,
        cost=None if own is None else money(own),
        cost_pct=cost_share(own, base),
        missing_recipe=own is None,
    )
    for modifier, options in _option_links(product):
        for option in options:
            price = base + Decimal(str(option.price or 0))
            option_cost = priced.unit_cost((_OPTION, option.id))
            # A product with no recipe of its own draws nothing for itself
            # (the sale warns instead), so its options carry the whole cost.
            total = None if option_cost is None else (own or Decimal(0)) + option_cost
            entry.options.append(
                OptionCost(
                    modifier_option_id=option.id,
                    modifier_name=modifier.name,
                    name=option.name,
                    price=money(price),
                    cost=None if total is None else money(total),
                    cost_pct=cost_share(total, price),
                )
            )
    return entry


async def product_costs(
    db: AsyncSession, product_ids: list[uuid.UUID]
) -> list[ProductCost]:
    if not product_ids:
        return []
    products = await _load_products(db, product_ids)
    priced = await _price(db, products)
    out = [_cost_of(product, priced) for product in products]
    order = {product_id: index for index, product_id in enumerate(product_ids)}
    out.sort(key=lambda entry: order.get(entry.product_id, len(order)))
    return out


@dataclass
class RecipeLineRow:
    """One leaf ingredient of a product's own recipe or of one option's."""

    product_id: uuid.UUID
    #: Null for the product's own recipe.
    modifier_name: str | None
    option_name: str | None
    #: Null when the owner has no active recipe (one placeholder row).
    item_name: str | None
    item_sku: str | None
    item_kind: str | None
    #: In the item's ingredient unit, per one product / one option.
    quantity: Decimal | None
    unit: str | None
    #: Per ingredient unit, at the same current cost as `ProductCost`.
    unit_cost: Decimal | None


@dataclass
class CostReport:
    products: list[Product]
    costs: dict[uuid.UUID, ProductCost]
    recipe_lines: list[RecipeLineRow]


async def catalogue_report(db: AsyncSession) -> CostReport:
    """Every active product's cost against price, and the recipe lines behind
    it — both from one pricing pass, so the two tabs of the export agree."""
    products = await _load_products(db, None)
    priced = await _price(db, products)
    lines: list[RecipeLineRow] = []

    def add(product, owner, modifier_name=None, option_name=None):
        expanded = priced.expansions.get(owner)
        if not expanded:
            lines.append(
                RecipeLineRow(product.id, modifier_name, option_name, *([None] * 6))
            )
            return
        for item_id, line in expanded.items():
            item = priced.catalog.items[item_id]
            lines.append(
                RecipeLineRow(
                    product_id=product.id,
                    modifier_name=modifier_name,
                    option_name=option_name,
                    item_name=item.name,
                    item_sku=item.sku,
                    item_kind=item.kind,
                    quantity=Decimal(str(line.quantity)),
                    unit=item.ingredient_unit,
                    unit_cost=priced.ingredient_cost(item_id),
                )
            )

    for product in products:
        if product.consumes_stock:
            add(product, (_PRODUCT, product.id))
        for modifier, options in _option_links(product):
            for option in options:
                add(product, (_OPTION, option.id), modifier.name, option.name)
    return CostReport(
        products=products,
        costs={p.id: _cost_of(p, priced) for p in products},
        recipe_lines=lines,
    )


#: The console list's cost sorts (`product_service.get_all`).
COST_SORTS = frozenset({"cost_asc", "cost_desc", "cost_pct_asc", "cost_pct_desc"})


def headline(entry: ProductCost) -> tuple[Decimal | None, Decimal | None]:
    """(cost, cost %) a product sorts by: its own, or — with options — those of
    its cheapest priced option, the one its "From" price shows (a free option is
    skipped, as `product_service._from_price` skips it)."""
    if entry.options:
        priced = [option for option in entry.options if option.price > 0]
        cheapest = min(priced or entry.options, key=lambda option: option.price)
        return cheapest.cost, cheapest.cost_pct
    return entry.cost, entry.cost_pct


def order_by_cost(
    entries: list[ProductCost], names: dict[uuid.UUID, str], sort: str
) -> list[uuid.UUID]:
    """Product ids in *sort* order. Unknown costs (no recipe) go last either way;
    the name breaks ties so a page boundary never shows a product twice."""
    use_pct = sort.startswith("cost_pct")
    descending = sort.endswith("_desc")
    known, unknown = [], []
    for entry in entries:
        cost, pct = headline(entry)
        value = pct if use_pct else cost
        (unknown if value is None else known).append((value, entry.product_id))
    by_name = lambda pair: names.get(pair[1], "")  # noqa: E731
    known.sort(key=by_name)
    known.sort(key=lambda pair: pair[0], reverse=descending)  # stable: name ties
    unknown.sort(key=by_name)
    return [product_id for _, product_id in known + unknown]
