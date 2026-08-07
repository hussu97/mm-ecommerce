"""
"Price: low to high" has to order by the price the customer can see.

The storefront prints a "From" price computed by walking a product's modifiers
(`apps/web/lib/pricing.ts::computeFromPrice`), because half this catalogue has
`base_price = 0` and is priced entirely through a size option — every brownie,
every cookie. The sort ordered by `base_price` alone, so on those pages it
ranked nine identical zeroes and handed back whatever order the planner chose,
underneath a control promising otherwise.

Nothing caught it because the API's `sort` parameter had no caller until a sort
control shipped, and because a unit test with a mocked session cannot tell you
what Postgres does with an ORDER BY. So this runs against a real database, and
it builds its own modifier-priced products rather than trusting a seeded
catalogue to contain the shape under test.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.product import Product
from app.services import product_service as svc

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)

TAG = "pytest-pricesort"


@pytest.fixture
async def session():
    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        yield db
        await db.rollback()
        for model, column in (
            (ProductModifier, None),
            (ModifierOption, None),
            (Modifier, Modifier.name),
            (Product, Product.name),
        ):
            if column is None:
                continue
            for row in (
                (await db.execute(select(model).where(column.like(f"{TAG}%"))))
                .scalars()
                .all()
            ):
                await db.delete(row)
        await db.commit()
    await engine.dispose()


async def _modifier_priced(
    session,
    label: str,
    option_prices: list[float],
    *,
    base: float = 0,
    required: bool = True,
) -> Product:
    """A product whose price lives in its options, the way a brownie's does."""
    suffix = uuid.uuid4().hex[:8]
    product = Product(
        name=f"{TAG}-{label}",
        slug=f"{TAG}-{label}-{suffix}",
        sku=f"{TAG.upper()}-{label.upper()}-{suffix}",
        base_price=base,
        is_active=True,
    )
    modifier = Modifier(
        reference=f"{TAG}-ref-{label}-{suffix}",
        name=f"{TAG}-size-{label}-{suffix}",
    )
    session.add_all([product, modifier])
    await session.flush()

    for i, price in enumerate(option_prices):
        session.add(
            ModifierOption(
                modifier_id=modifier.id,
                name=f"{TAG}-opt-{i}",
                sku=f"{TAG.upper()}-OPT-{suffix}-{i}",
                price=price,
                is_active=True,
            )
        )
    session.add(
        ProductModifier(
            product_id=product.id,
            modifier_id=modifier.id,
            minimum_options=1 if required else 0,
            maximum_options=1,
        )
    )
    await session.commit()
    return product


async def _sorted_names(session, sort: str) -> list[str]:
    items, _ = await svc.get_all(session, sort=sort, per_page=2000, channel="all")
    return [p.name for p in items if p.name.startswith(TAG)]


async def test_modifier_priced_products_sort_by_the_price_on_the_card(session):
    """
    The bug, exactly: three products with `base_price = 0` whose real prices are
    55, 40 and 70. Sorting on `base_price` sees 0, 0, 0.
    """
    await _modifier_priced(session, "mid", [55, 80])
    await _modifier_priced(session, "cheap", [40, 60])
    await _modifier_priced(session, "dear", [70, 90])

    assert await _sorted_names(session, "price_asc") == [
        f"{TAG}-cheap",
        f"{TAG}-mid",
        f"{TAG}-dear",
    ]
    assert await _sorted_names(session, "price_desc") == [
        f"{TAG}-dear",
        f"{TAG}-mid",
        f"{TAG}-cheap",
    ]


async def test_base_priced_and_modifier_priced_products_sort_together(session):
    """
    A catalogue holds both shapes at once, and they have to interleave by what
    the customer would pay — not by which shape they happen to be.
    """
    await _modifier_priced(session, "mods45", [45, 90])
    plain = Product(
        name=f"{TAG}-base30",
        slug=f"{TAG}-base30-{uuid.uuid4().hex[:8]}",
        sku=f"{TAG.upper()}-BASE30-{uuid.uuid4().hex[:8]}",
        base_price=30,
        is_active=True,
    )
    session.add(plain)
    await session.commit()

    assert await _sorted_names(session, "price_asc") == [
        f"{TAG}-base30",
        f"{TAG}-mods45",
    ]


async def test_a_required_option_raises_the_floor_above_the_base_price(session):
    """
    A required group is a choice the customer cannot decline, so its cheapest
    option is part of the floor — which is what `computeFromPrice` does, and
    what a card showing "From 35.00" on a 10-dirham base means.
    """
    await _modifier_priced(session, "base10plus25", [25, 40], base=10, required=True)
    await _modifier_priced(session, "base30flat", [5, 9], base=30, required=False)

    # 10 + 25 = 35 against a flat 30: the optional group must not lift the
    # second product's floor, and the required one must lift the first's.
    assert await _sorted_names(session, "price_asc") == [
        f"{TAG}-base30flat",
        f"{TAG}-base10plus25",
    ]


async def test_sorting_by_price_does_not_duplicate_multi_modifier_products(session):
    """
    The ordering is built from correlated subqueries rather than a join for this
    reason. A join against three modifier groups would return the product three
    times, and a customer paging a listing would meet the same cake twice while
    another disappeared off the end.
    """
    suffix = uuid.uuid4().hex[:8]
    product = Product(
        name=f"{TAG}-manygroups",
        slug=f"{TAG}-manygroups-{suffix}",
        sku=f"{TAG.upper()}-MANY-{suffix}",
        base_price=0,
        is_active=True,
    )
    session.add(product)
    await session.flush()
    for g in range(3):
        modifier = Modifier(
            reference=f"{TAG}-ref-g{g}-{suffix}",
            name=f"{TAG}-group{g}-{suffix}",
        )
        session.add(modifier)
        await session.flush()
        session.add(
            ModifierOption(
                modifier_id=modifier.id,
                name=f"{TAG}-o{g}",
                sku=f"{TAG.upper()}-O{g}-{suffix}",
                price=20 + g,
                is_active=True,
            )
        )
        session.add(
            ProductModifier(
                product_id=product.id,
                modifier_id=modifier.id,
                minimum_options=0,
                maximum_options=1,
            )
        )
    await session.commit()

    names = await _sorted_names(session, "price_asc")
    assert names.count(f"{TAG}-manygroups") == 1

    # And the count the pager divides by has to agree with that.
    _, total = await svc.get_all(
        session, sort="price_asc", per_page=2000, channel="all"
    )
    _, total_newest = await svc.get_all(
        session, sort="newest", per_page=2000, channel="all"
    )
    assert total == total_newest


async def test_an_inactive_option_is_not_a_price_anyone_can_pay(session):
    """
    A withdrawn size must not set the floor. Sorting a product to the top of
    "cheapest first" on an option nobody can select is an advertised price that
    does not exist.
    """
    await _modifier_priced(session, "activefloor", [60, 80])
    hidden = await _modifier_priced(session, "hiddencheap", [50, 90])

    # Withdraw the 50 — the product's real floor becomes 90.
    modifier_ids = (
        (
            await session.execute(
                select(ProductModifier.modifier_id).where(
                    ProductModifier.product_id == hidden.id
                )
            )
        )
        .scalars()
        .all()
    )
    option = (
        await session.execute(
            select(ModifierOption).where(
                ModifierOption.modifier_id.in_(modifier_ids),
                ModifierOption.price == 50,
            )
        )
    ).scalar_one()
    option.is_active = False
    await session.commit()

    assert await _sorted_names(session, "price_asc") == [
        f"{TAG}-activefloor",
        f"{TAG}-hiddencheap",
    ]
