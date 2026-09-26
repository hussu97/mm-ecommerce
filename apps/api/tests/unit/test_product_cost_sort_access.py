"""An ordering by cost discloses cost, so the cost sorts are staff-only and take
the permission that reads cost (`catalogue.recipes.read`)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.services.catalog.product_cost_service import (
    OptionCost,
    ProductCost,
    order_by_cost,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sort", ["cost_asc", "cost_desc", "cost_pct_asc", "cost_pct_desc"]
)
async def test_a_shopper_cannot_sort_by_cost(client, sort):
    response = await client.get(f"/api/v1/products?sort={sort}")
    assert response.status_code == 403


def _entry(name_id, cost=None, pct=None, options=()):
    return ProductCost(
        product_id=name_id,
        price=Decimal("10"),
        consumes_stock=True,
        cost=cost,
        cost_pct=pct,
        missing_recipe=cost is None,
        options=list(options),
    )


def _option(price, cost, pct):
    return OptionCost(
        modifier_option_id=uuid.uuid4(),
        modifier_name="Size",
        name=str(price),
        price=Decimal(price),
        cost=None if cost is None else Decimal(cost),
        cost_pct=None if pct is None else Decimal(pct),
    )


def test_order_by_cost_uses_the_cheapest_priced_option_and_puts_unknowns_last():
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    names = {a: "Alpha", b: "Bravo", c: "Charlie", d: "Delta"}
    entries = [
        _entry(a, Decimal("3"), Decimal("30")),
        # Free option skipped: the 20.00 option (cost 12, 60%) is the headline.
        _entry(b, options=[_option("0", None, None), _option("20", "12", "60")]),
        _entry(c),  # no recipe
        _entry(d, Decimal("3"), Decimal("10")),  # ties Alpha on cost: name order
    ]
    assert order_by_cost(entries, names, "cost_asc") == [a, d, b, c]
    assert order_by_cost(entries, names, "cost_desc") == [b, a, d, c]
    assert order_by_cost(entries, names, "cost_pct_asc") == [d, a, b, c]
    assert order_by_cost(entries, names, "cost_pct_desc") == [b, a, d, c]
