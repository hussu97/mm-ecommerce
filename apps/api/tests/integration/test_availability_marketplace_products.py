"""
The counter's stock screen lists what this branch sells online — website *and*
marketplaces.

The screen once read only `sells_on("web")`, so when the Barsha coffee went onto
the delivery apps through a Barsha-only integrator category (migration 320) it
never appeared, and nobody at the counter could take a drink off the apps. The
switch on that screen is the one the GrubOps push reads, so a marketplace item
missing from it has no off switch at all.

Each scenario runs inside one rolled-back transaction; nothing persists.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.v1.availability import _rows
from app.models import MenuGroup, MenuGroupProduct
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.menu import ROOT_KIND_INTEGRATOR
from app.models.product import Product
from app.services.catalog import menu_group_service

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
            yield session
        finally:
            await session.close()
            await trans.rollback()
    await engine.dispose()


async def _branch(db) -> Branch:
    tag = uuid.uuid4().hex[:10]
    branch = Branch(name=f"pytest-mkt {tag}", reference=f"MK{tag}")
    db.add(branch)
    await db.flush()
    return branch


async def _root(db) -> MenuGroup:
    root = await menu_group_service.integrator_root(db)
    if root is not None:
        return root
    root = MenuGroup(name="pytest integrator", root_kind=ROOT_KIND_INTEGRATOR)
    db.add(root)
    await db.flush()
    root.root_id = root.id
    await db.flush()
    return root


async def _category(db, root, *, scope=None, active=True, deleted=False):
    group = MenuGroup(
        name=f"pytest-mkt {uuid.uuid4().hex[:6]}",
        root_kind=ROOT_KIND_INTEGRATOR,
        root_id=root.id,
        parent_id=root.id,
        # The schema holds a deleted group inactive (ck_menu_groups_deleted_means_inactive).
        is_active=active and not deleted,
        deleted_at=utcnow() if deleted else None,
        marketplace_branch_ids=scope,
    )
    db.add(group)
    await db.flush()
    return group


async def _product(db, name, *, web=False, group=None) -> uuid.UUID:
    product = Product(
        name=f"pytest-mkt {name}",
        slug=f"pytest-mkt-{name}-{uuid.uuid4().hex[:8]}",
        base_price=Decimal("10.00"),
        is_active=True,
        sales_channels=["web"] if web else [],
    )
    db.add(product)
    await db.flush()
    if group is not None:
        db.add(MenuGroupProduct(group_id=group.id, product_id=product.id))
        await db.flush()
    return product.id


async def _listed(db, branch, products: dict[str, uuid.UUID]) -> set[str]:
    rows = await _rows(
        db, SimpleNamespace(branch_id=branch.id), list(products.values())
    )
    found = {r.id for r in rows}
    return {name for name, pid in products.items() if pid in found}


async def test_marketplace_only_products_are_listed_where_they_are_sold(db):
    barsha, sharjah = await _branch(db), await _branch(db)
    root = await _root(db)
    barsha_only = await _category(db, root, scope=[barsha.id])
    everywhere = await _category(db, root)
    off = await _category(db, root, active=False)
    gone = await _category(db, root, deleted=True)

    products = {
        "website": await _product(db, "website", web=True),
        "coffee": await _product(db, "coffee", group=barsha_only),
        "everywhere": await _product(db, "everywhere", group=everywhere),
        "inactive_category": await _product(db, "inactive", group=off),
        "deleted_category": await _product(db, "deleted", group=gone),
        "counter_only": await _product(db, "counter"),
    }

    assert await _listed(db, barsha, products) == {"website", "coffee", "everywhere"}
    assert await _listed(db, sharjah, products) == {"website", "everywhere"}


async def test_a_web_product_also_on_a_scoped_category_is_listed_once(db):
    barsha, sharjah = await _branch(db), await _branch(db)
    root = await _root(db)
    barsha_only = await _category(db, root, scope=[barsha.id])
    both = await _product(db, "both", web=True, group=barsha_only)

    for branch in (barsha, sharjah):
        rows = await _rows(db, SimpleNamespace(branch_id=branch.id), [both])
        assert [r.id for r in rows] == [both]
