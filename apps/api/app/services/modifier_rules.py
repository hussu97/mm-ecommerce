"""
What a customer may choose from a product's modifier groups, and what it costs.

One implementation for the website and the register. Before this, the cart
validated the choice rules and the till did not: `pos_order_service.add_item`
summed whatever prices the terminal sent it and never counted the picks
against `minimum_options` / `maximum_options`. A till is a trusted device
right up until it is a tablet on a counter, and "the client already checked"
is not a control — it is the absence of one.

The rules are Foodics', on the product↔modifier link rather than the group,
because the same "Milk" group is optional on a brownie and required on a
latte:

* **minimum / maximum options** bound the *total quantity* chosen in the
  group, not the number of distinct options. That is what makes a mixed box
  work: "Mix Brownies Box Of 3" is min 3 / max 3, and two Ferrero plus one
  Fudge is three.
* **free options** are the first N units in the group at no charge.
* **unique options** forbids repeating an option. A single-select group
  (`maximum_options <= 1`) is unique whether or not the flag says so.

Prices always come from the catalogue row, never from the request.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.exceptions import BadRequestError
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.product import Product


@dataclass(frozen=True)
class Selection:
    """One option the customer picked, and how many of it."""

    option_id: uuid.UUID
    quantity: int = 1
    #: What the client believed the option belonged to. Advisory only — the
    #: group is resolved from the option, so a client that gets this wrong
    #: gets the right answer rather than a spurious rejection.
    modifier_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ResolvedOption:
    """A validated pick, priced from the catalogue."""

    modifier_id: uuid.UUID
    modifier_name: str
    modifier_translations: dict
    option_id: uuid.UUID
    option_name: str
    option_translations: dict
    option_sku: str
    #: Catalogue price for one of these.
    unit_price: Decimal
    quantity: int
    #: `unit_price` times the units that are not free. This is what the line
    #: is charged; it is not `unit_price * quantity` when the group grants
    #: free options.
    charged_total: Decimal


def effective_unique(link: ProductModifier) -> bool:
    """
    Whether repeating an option is forbidden for this link.

    A group you may only pick one of cannot repeat by construction, so the
    flag is not the only source — otherwise a link created with the flag off
    and `maximum_options` 1 would accept "two of the same size".
    """
    return bool(link.unique_options) or link.maximum_options <= 1


def total_quantity(selections: list[Selection]) -> int:
    return sum(s.quantity for s in selections)


def describe_requirement(link: ProductModifier, name: str) -> str:
    """
    The one sentence every client uses for an unmet group.

    Shared so the website, the iPad and the iPhone say the same thing to the
    same customer — the phrase gets read out over a counter.
    """
    minimum, maximum = link.minimum_options, link.maximum_options
    if minimum and minimum == maximum:
        return f"Choose exactly {minimum} from {name}"
    if minimum:
        return f"Choose between {minimum} and {maximum} from {name}"
    return f"Choose up to {maximum} from {name}"


async def resolve(
    db: AsyncSession,
    *,
    product: Product,
    selections: list[Selection],
) -> list[ResolvedOption]:
    """
    Validate `selections` against `product`'s groups and price them.

    Raises `BadRequestError` with a sentence meant for a cashier or a shopper,
    not a stack trace. Returns the picks in group order, then option order, so
    a receipt and a kitchen ticket list them the same way every time.
    """
    links = list(
        (
            await db.execute(
                select(ProductModifier)
                .where(ProductModifier.product_id == product.id)
                .options(
                    joinedload(ProductModifier.modifier).selectinload(Modifier.options)
                )
                .order_by(ProductModifier.display_order)
            )
        )
        .scalars()
        .unique()
    )

    #: option id → (link, option), so a pick resolves to its group without
    #: trusting the modifier_id the client sent.
    catalogue: dict[uuid.UUID, tuple[ProductModifier, ModifierOption]] = {}
    for link in links:
        if not link.modifier.is_active:
            continue
        for option in link.modifier.options:
            catalogue[option.id] = (link, option)

    # Collapse repeats. A client may express "two Ferrero" either as a
    # quantity of 2 or as the option twice; both have to mean the same thing,
    # or the same basket prices differently depending on which client built it.
    merged: dict[uuid.UUID, int] = {}
    for selection in selections:
        if selection.quantity < 1:
            raise BadRequestError("An option quantity must be at least 1")
        if selection.option_id not in catalogue:
            raise BadRequestError(f"That option is not available on {product.name}")
        merged[selection.option_id] = (
            merged.get(selection.option_id, 0) + selection.quantity
        )

    by_link: dict[uuid.UUID, list[tuple[ModifierOption, int]]] = {}
    for option_id, quantity in merged.items():
        link, option = catalogue[option_id]
        by_link.setdefault(link.id, []).append((option, quantity))

    resolved: list[ResolvedOption] = []
    for link in links:
        picks = by_link.get(link.id, [])
        name = link.modifier.name
        chosen = sum(quantity for _, quantity in picks)

        if chosen < link.minimum_options or chosen > link.maximum_options:
            raise BadRequestError(describe_requirement(link, name))
        if effective_unique(link) and any(quantity > 1 for _, quantity in picks):
            raise BadRequestError(f"You can only choose each option once in {name}")

        # Free options are counted across the group in the order the options
        # are laid out, so which picks are free never depends on the order the
        # client happened to send them in.
        picks.sort(key=lambda pair: (pair[0].display_order, pair[0].name))
        free_left = max(link.free_options, 0)
        for option, quantity in picks:
            free_here = min(free_left, quantity)
            free_left -= free_here
            unit_price = Decimal(str(option.price))
            resolved.append(
                ResolvedOption(
                    modifier_id=link.modifier_id,
                    modifier_name=name,
                    modifier_translations=link.modifier.translations or {},
                    option_id=option.id,
                    option_name=option.name,
                    option_translations=option.translations or {},
                    option_sku=option.sku,
                    unit_price=unit_price,
                    quantity=quantity,
                    charged_total=unit_price * (quantity - free_here),
                )
            )

    return resolved
