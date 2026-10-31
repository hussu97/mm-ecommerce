"""
The website union over resolved ids is the same predicate as the inlined one.

`out_at_every_branch_subquery` / `out_at_every_branch_in_set_subquery` inline
the branch set as a subquery — three times per catalogue query, planning cost
~4× execution. `resolve_union_branch_ids` + `out_at_every_of_ids` resolve the
set once and pass literal ids. That is only an optimisation if every answer is
identical, including both fallbacks (an active map with no assignments falls
back to every online branch; a pin whose serving branches are all off falls
back to the estate union) and the empty-set guard (no branches hides nothing).

Each scenario runs inside one rolled-back transaction, so the rest of the
estate in the test database is switched off for the duration and nothing
persists.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.base import utcnow
from app.models.branch import Branch
from app.models.delivery_polygon import DeliveryPolygon, DeliveryPolygonVersion
from app.models.menu import BranchProduct
from app.models.polygon_branch_fulfilment import PolygonBranchFulfilment
from app.models.product import Product
from app.services.catalog import availability_service, product_service
from app.services.catalog.storefront_visibility import (
    resolve_union_ids,
    website_availability_clause,
)

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]


@pytest.fixture
async def db():
    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            # Only this test's world is live: every other branch off, every map
            # version inactive. All of it rolls back.
            await session.execute(update(Branch).values(is_active=False))
            await session.execute(
                update(DeliveryPolygonVersion).values(is_active=False)
            )
            yield session
        finally:
            await session.close()
            await trans.rollback()
    await engine.dispose()


async def _branch(db, *, online=True, active=True, busy=False) -> Branch:
    tag = uuid.uuid4().hex[:10]
    branch = Branch(
        name=f"pytest-union {tag}",
        reference=f"UN{tag}",
        receives_online_orders=online,
        is_active=active,
        busy_since=utcnow() if busy else None,
    )
    db.add(branch)
    await db.flush()
    return branch


async def _products(db, branches) -> dict[str, uuid.UUID]:
    """Five products with every shape of exception row across the branches."""
    b1, b2, b3 = branches
    tag = uuid.uuid4().hex[:8]
    names = ("none", "out_b1", "out_b1_b2", "lapsed_b2", "inactive_all", "out_b3")
    products = {}
    for name in names:
        p = Product(
            name=f"pytest-union {name}",
            slug=f"pytest-union-{name}-{tag}",
            base_price=Decimal("10.00"),
            is_active=True,
        )
        db.add(p)
        products[name] = p
    await db.flush()
    now = utcnow()

    def out(product, branch, **kw):
        db.add(
            BranchProduct(
                branch_id=branch.id,
                product_id=products[product].id,
                is_in_stock=kw.get("in_stock", False),
                is_active=kw.get("is_active", True),
                out_of_stock_until=kw.get("until"),
                unavailable_source=None if kw.get("in_stock") else "staff",
            )
        )

    out("out_b1", b1)
    out("out_b1_b2", b1)
    out("out_b1_b2", b2, until=now + timedelta(hours=1))
    out("lapsed_b2", b2, until=now - timedelta(minutes=5))
    for b in (b1, b2, b3):
        out("inactive_all", b, in_stock=True, is_active=False)
    out("out_b3", b3)
    await db.flush()
    return {k: v.id for k, v in products.items()}


async def _map(db, assigned: list[Branch]) -> uuid.UUID:
    version = DeliveryPolygonVersion(name="pytest-union", is_active=True)
    db.add(version)
    await db.flush()
    polygon = DeliveryPolygon(
        version_id=version.id,
        name="pytest-union zone",
        delivery_fee=Decimal("10.00"),
        geometry={"type": "Polygon", "coordinates": []},
        min_lat=Decimal("25"),
        max_lat=Decimal("26"),
        min_lng=Decimal("55"),
        max_lng=Decimal("56"),
    )
    db.add(polygon)
    await db.flush()
    for rank, branch in enumerate(assigned, start=1):
        db.add(
            PolygonBranchFulfilment(
                polygon_id=polygon.id, branch_id=branch.id, rank=rank
            )
        )
    await db.flush()
    return polygon.id


async def _hidden(db, products, clause) -> set[str]:
    rows = await db.execute(
        select(Product.id).where(Product.id.in_(products.values()), ~clause)
    )
    found = set(rows.scalars())
    return {name for name, pid in products.items() if pid in found}


async def _both(db, products, branch_ids=None) -> tuple[set[str], set[str]]:
    """(hidden by the inline predicate, hidden by the resolved-ids one)."""
    inline = website_availability_clause(None, branch_ids=branch_ids)
    ids = await resolve_union_ids(db, None, branch_ids=branch_ids)
    resolved = website_availability_clause(None, branch_ids=branch_ids, union_ids=ids)
    return await _hidden(db, products, inline), await _hidden(db, products, resolved)


async def test_empty_map_falls_back_to_every_online_branch(db):
    b1, b2 = await _branch(db), await _branch(db)
    b3 = await _branch(db, online=False)
    products = await _products(db, (b1, b2, b3))
    # No active map assignments at all: the set is b1+b2 (b3 is not online).
    inline, resolved = await _both(db, products)
    assert inline == resolved == {"out_b1_b2", "inactive_all"}


async def test_assigned_branches_only_once_the_map_has_any(db):
    b1, b2 = await _branch(db), await _branch(db)
    b3 = await _branch(db, online=False)
    products = await _products(db, (b1, b2, b3))
    await _map(db, [b1])
    inline, resolved = await _both(db, products)
    assert inline == resolved == {"out_b1", "out_b1_b2", "inactive_all"}


async def test_a_busy_branch_drops_out_of_the_union(db):
    b1, b2 = await _branch(db), await _branch(db, busy=True)
    b3 = await _branch(db, online=False)
    products = await _products(db, (b1, b2, b3))
    inline, resolved = await _both(db, products)
    assert inline == resolved == {"out_b1", "out_b1_b2", "inactive_all"}


async def test_no_branch_at_all_hides_nothing(db):
    b1 = await _branch(db, active=False)
    b2 = await _branch(db, online=False)
    b3 = await _branch(db, busy=True)
    products = await _products(db, (b1, b2, b3))
    inline, resolved = await _both(db, products)
    assert inline == resolved == set()
    assert await availability_service.resolve_union_branch_ids(db) == []


async def test_a_pin_narrows_to_its_serving_branches(db):
    b1, b2 = await _branch(db), await _branch(db)
    b3 = await _branch(db)
    products = await _products(db, (b1, b2, b3))
    for pin in ([b1.id], [b2.id], [b1.id, b2.id], [b3.id], [b1.id, b2.id, b3.id]):
        inline, resolved = await _both(db, products, branch_ids=pin)
        assert inline == resolved, pin


async def test_a_pin_whose_branches_are_all_off_falls_back_to_the_estate(db):
    b1, b2 = await _branch(db), await _branch(db)
    b3 = await _branch(db, online=False)
    products = await _products(db, (b1, b2, b3))
    inline, resolved = await _both(db, products, branch_ids=[b3.id])
    # The estate union (b1+b2), not b3's own stockouts.
    assert inline == resolved == {"out_b1_b2", "inactive_all"}


async def test_an_empty_pin_list_is_the_false_predicate_both_ways(db):
    assert await availability_service.resolve_union_branch_ids(db, []) == []
    b1, b2, b3 = await _branch(db), await _branch(db), await _branch(db)
    products = await _products(db, (b1, b2, b3))
    inline = ~availability_service.out_at_every_branch_in_set_subquery([])
    resolved = ~availability_service.out_at_every_of_ids([])
    assert (
        await _hidden(db, products, inline)
        == await _hidden(db, products, resolved)
        == set()
    )


async def test_product_service_listing_and_page_agree_with_the_inline_rule(db):
    """End to end through the service: the list and the page's `is_available`
    are what the inline predicate says, for the estate and for a pin."""
    b1, b2 = await _branch(db), await _branch(db)
    b3 = await _branch(db, online=False)
    products = await _products(db, (b1, b2, b3))
    polygon_ids = [b1.id]
    for branch_ids in (None, polygon_ids):
        inline_hidden = await _hidden(
            db, products, website_availability_clause(None, branch_ids=branch_ids)
        )
        for name, pid in products.items():
            product = await db.get(Product, pid)
            page = await product_service.get_by_slug(
                db, product.slug, branch_ids=branch_ids
            )
            assert page.is_available is (name not in inline_hidden), (
                name,
                branch_ids,
            )
