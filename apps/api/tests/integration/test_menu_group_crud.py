"""
Menu group writes, against a real database.

These exist because two bugs got all the way to production behind a green
suite, and neither was the sort of thing source inspection can see:

  * `_set_products` read `group.members` on a group that had only just been
    added and flushed. The relationship was unloaded, so touching it emitted a
    lazy load — which raises under asyncio instead of returning the empty list
    it appears it should. Every attempt to create a group 500'd.
  * The session runs with `expire_on_commit=False`, so re-reading a group
    straight after writing it handed back the collection it was loaded with
    rather than the one just saved. The write landed; the response described
    the state before it.

Both need a live session and a real round trip to catch, so this module skips
itself when there is no database to talk to.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import MenuGroup, MenuGroupProduct, Product
from app.services.catalog import menu_group_service as svc

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)


@pytest.fixture
async def session():
    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        yield db
        # Leave nothing behind, whatever the test did.
        await db.rollback()
        for row in (
            (await db.execute(select(MenuGroup).where(MenuGroup.name.like("pytest-%"))))
            .scalars()
            .all()
        ):
            await db.delete(row)
        await db.commit()
    await engine.dispose()


@pytest.fixture
async def branch_root(session) -> MenuGroup:
    """A shop's menu root — every test group hangs under this now.

    A top-level group is a *root* and a branch root must name its branch, so the
    CRUD these tests exercise happens one level down, inside a shop's tree, the
    same way the console builds it. Migration 191 seeds a root for every active
    branch, so this borrows one rather than minting a branch (which an inventory
    trigger requires a default stock container for). The test groups it hangs are
    named `pytest-%` and cleaned up by the `session` fixture.
    """
    root = (
        await session.execute(
            select(MenuGroup)
            .where(
                MenuGroup.parent_id.is_(None),
                MenuGroup.root_kind == "branch",
                MenuGroup.deleted_at.is_(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if root is None:
        pytest.skip("no branch menu root in this database")
    yield root


@pytest.fixture
async def product_ids(session) -> list[uuid.UUID]:
    """
    Three products, created here rather than borrowed from the database.

    A CI database is empty, and a fixture that skips when it finds nothing
    turns this whole module into a no-op exactly where it is needed most.
    """
    made = []
    for n in range(3):
        product = Product(
            name=f"pytest-product-{n}",
            slug=f"pytest-product-{n}",
            sku=f"PYTEST{n}",
            base_price=10,
        )
        session.add(product)
        made.append(product)
    await session.flush()
    ids = [p.id for p in made]
    await session.commit()

    yield ids

    # Groups first: deleting a product still filed in one leaves SQLAlchemy
    # deleting link rows the database has already cascaded away.
    for group in (
        (
            await session.execute(
                select(MenuGroup).where(MenuGroup.name.like("pytest-%"))
            )
        )
        .scalars()
        .all()
    ):
        await session.delete(group)
    await session.flush()
    for product in made:
        await session.delete(product)
    await session.commit()


async def test_creating_a_group_with_products_does_not_blow_up(
    session, branch_root, product_ids
):
    """The lazy-load-in-async case: this 500'd in production."""
    group = await svc.create(
        session,
        {
            "name": "pytest-create",
            "parent_id": branch_root.id,
            "product_ids": product_ids[:2],
        },
    )
    assert len(group.members) == 2


async def test_the_response_describes_the_state_just_saved(
    session, branch_root, product_ids
):
    """The stale-read case: the write landed, the response predated it."""
    group = await svc.create(
        session,
        {
            "name": "pytest-stale",
            "parent_id": branch_root.id,
            "product_ids": product_ids[:2],
        },
    )
    group = await svc.update(session, group.id, {"product_ids": product_ids})

    in_database = (
        await session.execute(
            select(func.count())
            .select_from(MenuGroupProduct)
            .where(MenuGroupProduct.group_id == group.id)
        )
    ).scalar_one()
    assert len(group.members) == in_database == 3


async def test_removing_a_product_takes_effect(session, branch_root, product_ids):
    group = await svc.create(
        session,
        {
            "name": "pytest-remove",
            "parent_id": branch_root.id,
            "product_ids": product_ids,
        },
    )
    group = await svc.update(session, group.id, {"product_ids": product_ids[:1]})
    assert len(group.members) == 1


async def test_a_group_nests_and_the_tree_reports_it(session, branch_root, product_ids):
    parent = await svc.create(
        session,
        {"name": "pytest-parent", "parent_id": branch_root.id, "product_ids": []},
    )
    child = await svc.create(
        session,
        {
            "name": "pytest-child",
            "parent_id": parent.id,
            "product_ids": product_ids[:1],
        },
    )
    assert child.parent_id == parent.id

    tree = await svc.list_tree(session, branch_id=branch_root.branch_id)
    root = next(n for n in tree if n["id"] == branch_root.id)
    node = next(c for c in root["children"] if c["name"] == "pytest-parent")
    assert [c["name"] for c in node["children"]] == ["pytest-child"]


async def test_a_cycle_is_refused(session, branch_root):
    from app.core.exceptions import BadRequestError

    parent = await svc.create(
        session,
        {"name": "pytest-cyc-a", "parent_id": branch_root.id, "product_ids": []},
    )
    child = await svc.create(
        session, {"name": "pytest-cyc-b", "parent_id": parent.id, "product_ids": []}
    )
    with pytest.raises(BadRequestError):
        await svc.update(session, parent.id, {"parent_id": child.id})


async def test_deleting_a_parent_takes_its_children(session, branch_root):
    parent = await svc.create(
        session,
        {"name": "pytest-del-a", "parent_id": branch_root.id, "product_ids": []},
    )
    await svc.create(
        session, {"name": "pytest-del-b", "parent_id": parent.id, "product_ids": []}
    )
    await svc.delete(session, parent.id)

    left = (
        (
            await session.execute(
                select(MenuGroup).where(
                    MenuGroup.name.like("pytest-del-%"),
                    MenuGroup.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    assert left == [], "a child left behind is unreachable from any root"


async def test_an_unknown_product_is_rejected(session, branch_root):
    from app.core.exceptions import BadRequestError

    with pytest.raises(BadRequestError, match="No such product"):
        await svc.create(
            session,
            {
                "name": "pytest-bad",
                "parent_id": branch_root.id,
                "product_ids": [uuid.uuid4()],
            },
        )
