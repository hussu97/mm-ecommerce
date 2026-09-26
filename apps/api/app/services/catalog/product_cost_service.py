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
with their modifiers and options (selectin), the warehouse ids, the recipe
catalogue, and one ingredient-cost query.
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


async def product_costs(
    db: AsyncSession, product_ids: list[uuid.UUID]
) -> list[ProductCost]:
    if not product_ids:
        return []
    products = (
        (
            await db.execute(
                select(Product)
                .where(Product.id.in_(product_ids))
                .options(
                    selectinload(Product.product_modifiers)
                    .selectinload(ProductModifier.modifier)
                    .selectinload(Modifier.options)
                )
            )
        )
        .scalars()
        .all()
    )
    owners: set[tuple[str, uuid.UUID]] = set()
    for product in products:
        owners.add((_PRODUCT, product.id))
        for link in product.product_modifiers:
            owners.update(
                (_OPTION, option.id)
                for option in link.modifier.options
                if option.is_active
            )
    warehouse_ids = list(
        (await db.execute(select(Warehouse.id).where(Warehouse.is_active))).scalars()
    )
    costs = await recipe_service.owner_unit_costs(
        db, owners, warehouse_ids=warehouse_ids
    )

    out: list[ProductCost] = []
    for product in products:
        base = Decimal(str(product.base_price or 0))
        own = (
            costs.get((_PRODUCT, product.id)) if product.consumes_stock else Decimal(0)
        )
        entry = ProductCost(
            product_id=product.id,
            price=money(base),
            consumes_stock=product.consumes_stock,
            cost=None if own is None else money(own),
            cost_pct=cost_share(own, base),
            missing_recipe=own is None,
        )
        links = sorted(product.product_modifiers, key=lambda link: link.display_order)
        for link in links:
            options = sorted(
                (o for o in link.modifier.options if o.is_active),
                key=lambda o: (o.display_order, o.name),
            )
            for option in options:
                price = base + Decimal(str(option.price or 0))
                option_cost = costs.get((_OPTION, option.id))
                # A product with no recipe of its own draws nothing for itself
                # (the sale warns instead), so its options carry the whole cost.
                total = (
                    None if option_cost is None else (own or Decimal(0)) + option_cost
                )
                entry.options.append(
                    OptionCost(
                        modifier_option_id=option.id,
                        modifier_name=link.modifier.name,
                        name=option.name,
                        price=money(price),
                        cost=None if total is None else money(total),
                        cost_pct=cost_share(total, price),
                    )
                )
        out.append(entry)
    order = {product_id: index for index, product_id in enumerate(product_ids)}
    out.sort(key=lambda entry: order.get(entry.product_id, len(order)))
    return out
