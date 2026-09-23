"""Auto off-sale from produced-good stock, end to end against real Postgres.

The posting hook (dirty marks only for produced goods at enabled branches), and
the whole lifecycle through the scheduler's `tick`: auto-off when a produced good
hits zero, a staff override that wins until restock, auto-on when the stock
recovers, release when the item leaves the recipe, release when the feature is
switched off — each one audited, with the system or the person as the actor.

Runs inside one outer transaction that is rolled back; the session commits to
savepoints, so `tick`'s per-branch commits are real commits as far as it can
tell and nothing survives the test.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.audit_log import AuditLog
from app.models.branch import Branch
from app.models.inventory import InventoryItem, Warehouse
from app.models.inventory_v2 import BranchInventorySettings, InventoryAvailabilityDirty
from app.models.menu import (
    BranchModifierOption,
    BranchProduct,
    MenuGroup,
    MenuGroupProduct,
)
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.product import Product
from app.models.user import User
from app.services.catalog import availability_service as availability
from app.services.inventory import auto_availability_service as auto
from app.services.inventory import recipe_service
from app.services.inventory.recipe_service import RecipeLineInput

from ._stock import seed_stock

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-autoavail"
SYSTEM = uuid.UUID(int=0)


@pytest.fixture
async def db():
    # Every test rolls back, so the recipe generation repeats with different
    # recipes behind it — the drain's generation-keyed cache must not survive.
    auto._leaves_cache = None
    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()
    await engine.dispose()


def _tag() -> str:
    return uuid.uuid4().hex[:8]


async def _branch(
    db, *, enabled: bool
) -> tuple[Branch, Warehouse, BranchInventorySettings]:
    branch = Branch(name=f"{MARKER} {_tag()}", reference=f"{MARKER}-{_tag()}")
    db.add(branch)
    await db.flush()
    warehouse = Warehouse(branch_id=branch.id, name="Store", is_default=True)
    settings = BranchInventorySettings(
        branch_id=branch.id,
        inventory_enabled=True,
        auto_availability_enabled=enabled,
    )
    db.add_all([warehouse, settings])
    await db.flush()
    return branch, warehouse, settings


def _inventory_item(kind: str) -> InventoryItem:
    return InventoryItem(
        sku=f"{MARKER}-{_tag()}",
        name=f"{MARKER} {kind} {_tag()}",
        kind=kind,
        tracking_mode="stocked",
    )


@pytest.fixture
async def world(db):
    user = User(email=f"{MARKER}-{_tag()}@example.com", is_staff=True)
    db.add(user)
    branch, warehouse, settings = await _branch(db, enabled=True)
    cake = _inventory_item("produced_good")
    box = _inventory_item("packaging")
    db.add_all([cake, box])

    kunafa = Product(name="Kunafa", slug=f"{MARKER}-{_tag()}", sales_channels=["web"])
    brownies = Product(
        name="Brownies",
        slug=f"{MARKER}-{_tag()}",
        sales_channels=["web"],
        consumes_stock=False,
    )
    filling = Modifier(reference=f"{MARKER}-{_tag()}", name="Filling")
    db.add_all([kunafa, brownies, filling])
    await db.flush()
    lotus = ModifierOption(
        modifier_id=filling.id, name="Lotus", sku=f"{MARKER}-{_tag()}"
    )
    db.add(lotus)
    # One shared modifier on two products: its option is one row per branch.
    db.add_all(
        [
            ProductModifier(product_id=kunafa.id, modifier_id=filling.id),
            ProductModifier(product_id=brownies.id, modifier_id=filling.id),
        ]
    )
    root = MenuGroup(name="Menu", root_kind="branch", branch_id=branch.id)
    db.add(root)
    await db.flush()
    db.add_all(
        [
            MenuGroupProduct(group_id=root.id, product_id=kunafa.id),
            MenuGroupProduct(group_id=root.id, product_id=brownies.id),
        ]
    )
    await db.flush()

    for kind, owner_id, lines in (
        ("product", kunafa.id, [(cake, "1"), (box, "1")]),
        ("product", brownies.id, [(cake, "1")]),  # consumes_stock=False: skipped
        ("modifier_option", lotus.id, [(cake, "0.5")]),
    ):
        await recipe_service.draft_and_activate(
            db,
            kind=kind,
            owner_id=owner_id,
            lines=[
                RecipeLineInput(item_id=i.id, quantity=Decimal(q)) for i, q in lines
            ],
            user_id=user.id,
        )
    await db.commit()
    return {
        "user": user,
        "branch": branch,
        "warehouse": warehouse,
        "settings": settings,
        "cake": cake,
        "box": box,
        "kunafa": kunafa,
        "brownies": brownies,
        "lotus": lotus,
    }


async def _stock(db, w, item, qty):
    return await seed_stock(
        db,
        branch_id=w["branch"].id,
        warehouse_id=w["warehouse"].id,
        item_id=item.id,
        quantity=qty,
    )


async def _dirty(db, branch_id):
    return {
        row.item_id: row
        for row in (
            await db.execute(
                select(InventoryAvailabilityDirty).where(
                    InventoryAvailabilityDirty.branch_id == branch_id
                )
            )
        )
        .scalars()
        .all()
    }


async def _product_row(db, w, product):
    return (
        await db.execute(
            select(BranchProduct)
            .where(
                BranchProduct.branch_id == w["branch"].id,
                BranchProduct.product_id == product.id,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _option_row(db, w, option):
    return (
        await db.execute(
            select(BranchModifierOption)
            .where(
                BranchModifierOption.branch_id == w["branch"].id,
                BranchModifierOption.modifier_option_id == option.id,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _audits(db, entity_type):
    return list(
        (
            await db.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity_type == entity_type,
                    AuditLog.entity_label.like(f"%{MARKER}%"),
                )
                .order_by(AuditLog.created_at)
            )
        )
        .scalars()
        .all()
    )


def _changes(reports):
    return {
        (change.name, change.in_stock, change.reason)
        for report in reports
        for change in report.changes
    }


# ─── The posting hook ────────────────────────────────────────────────────────


async def test_the_hook_marks_only_produced_goods_at_enabled_branches(db, world):
    w = world
    await db.execute(
        InventoryAvailabilityDirty.__table__.delete().where(
            InventoryAvailabilityDirty.branch_id == w["branch"].id
        )
    )
    txn = await _stock(db, w, w["cake"], "4")
    await _stock(db, w, w["box"], "10")
    marks = await _dirty(db, w["branch"].id)
    assert set(marks) == {w["cake"].id}, "packaging must not be marked"
    assert marks[w["cake"].id].last_txn_id == txn.id

    off_branch, off_warehouse, _ = await _branch(db, enabled=False)
    await seed_stock(
        db,
        branch_id=off_branch.id,
        warehouse_id=off_warehouse.id,
        item_id=w["cake"].id,
        quantity="4",
    )
    assert await _dirty(db, off_branch.id) == {}

    # A second posting re-stamps rather than duplicating.
    before = marks[w["cake"].id].marked_at
    await _stock(db, w, w["cake"], "3")
    again = await _dirty(db, w["branch"].id)
    await db.refresh(again[w["cake"].id])
    assert again[w["cake"].id].marked_at > before


# ─── The lifecycle ───────────────────────────────────────────────────────────


async def test_the_whole_lifecycle(db, world):
    w = world
    branch, user = w["branch"], w["user"]

    # Stock on hand: nothing to do.
    await _stock(db, w, w["cake"], "2")
    await db.commit()
    assert _changes(await auto.tick(db, full=True)) == set()
    assert await _dirty(db, branch.id) == {}, "a full sweep clears what it read"

    # The cake runs out → the product and the (shared) option go off. The
    # non-consuming product is skipped even though its recipe names the cake.
    await _stock(db, w, w["cake"], "0")
    await db.commit()
    reports = await auto.tick(db, full=False)
    assert _changes(reports) == {
        ("Kunafa", False, "stock_depleted"),
        ("Brownies / Kunafa — Lotus", False, "stock_depleted"),
    }
    row = await _product_row(db, w, w["kunafa"])
    assert (row.is_in_stock, row.unavailable_source, row.out_of_stock_until) == (
        False,
        "auto",
        None,
    )
    assert row.auto_state["items"][0]["item_id"] == str(w["cake"].id)
    assert await _product_row(db, w, w["brownies"]) is None
    (email_row,) = [r for r in reports[0].email_rows if r["name"] == "Kunafa"]
    (trigger,) = email_row["items"]
    assert trigger["on_hand"] == Decimal("0")
    assert trigger["movement"]["type"] == "opening_balance"

    audits = await _audits(db, "branch_product")
    assert audits[-1].admin_id == SYSTEM
    assert audits[-1].admin_email == "system:auto-availability"
    assert audits[-1].changes["reason"] == "stock_depleted"
    assert (await _audits(db, "branch_modifier_option"))[-1].admin_id == SYSTEM

    # Staff puts the product back: staff wins until restock.
    await availability.set_product_stock(
        db, branch=branch, product_id=w["kunafa"].id, in_stock=True, actor=user
    )
    await db.commit()
    row = await _product_row(db, w, w["kunafa"])
    assert row.is_in_stock is True and row.staff_override_until_restock is True
    staff_audit = (await _audits(db, "branch_product"))[-1]
    assert staff_audit.admin_id == user.id
    assert staff_audit.changes["reason"] == "staff"

    assert _changes(await auto.tick(db, full=True)) == set(), "override holds"
    assert (await _product_row(db, w, w["kunafa"])).is_in_stock is True

    # Restock: the option comes back; the product's override clears silently.
    await _stock(db, w, w["cake"], "3")
    await db.commit()
    assert _changes(await auto.tick(db, full=False)) == {
        ("Brownies / Kunafa — Lotus", True, "stock_recovered"),
    }
    row = await _product_row(db, w, w["kunafa"])
    assert row.is_in_stock is True and row.staff_override_until_restock is False
    option_row = await _option_row(db, w, w["lotus"])
    assert (option_row.is_in_stock, option_row.unavailable_source) == (True, None)

    # Out again, then the product's recipe drops the cake → released.
    await _stock(db, w, w["cake"], "0")
    await db.commit()
    assert _changes(await auto.tick(db, full=False)) == {
        ("Kunafa", False, "stock_depleted"),
        ("Brownies / Kunafa — Lotus", False, "stock_depleted"),
    }
    await recipe_service.draft_and_activate(
        db,
        kind="product",
        owner_id=w["kunafa"].id,
        lines=[RecipeLineInput(item_id=w["box"].id, quantity=Decimal("1"))],
        user_id=user.id,
    )
    await db.commit()
    assert w["cake"].id in await _dirty(db, branch.id), "activation marks"
    assert _changes(await auto.tick(db, full=False)) == {
        ("Kunafa", True, "removed_from_recipe"),
    }

    # Feature switched off → every auto row at the branch is released.
    w["settings"].auto_availability_enabled = False
    await db.commit()
    assert _changes(await auto.tick(db, full=False)) == {
        ("Brownies / Kunafa — Lotus", True, "feature_disabled"),
    }
    option_row = await _option_row(db, w, w["lotus"])
    assert option_row.is_in_stock is True
    assert (await _audits(db, "branch_modifier_option"))[-1].changes[
        "reason"
    ] == "feature_disabled"


async def test_stock_short_of_one_sale_takes_only_that_owner_off(db, world):
    """0.7 of the cake: Kunafa (needs 1) cannot be sold, Lotus (needs 0.5) can
    — the 9-piece box with 5 brownies left, in miniature."""
    w = world
    await _stock(db, w, w["cake"], "0.7")
    await db.commit()
    assert _changes(await auto.tick(db, full=True)) == {
        ("Kunafa", False, availability.REASON_STOCK_DEPLETED)
    }
    lotus = await _option_row(db, w, w["lotus"])
    assert lotus is None or lotus.is_in_stock is True

    await _stock(db, w, w["cake"], "1")
    await db.commit()
    assert _changes(await auto.tick(db, full=True)) == {
        ("Kunafa", True, availability.REASON_STOCK_RECOVERED)
    }


async def test_a_website_only_product_is_sold_at_an_online_branch(db, world):
    """On no POS menu, but the branch takes online orders and bakes it: its
    stock still decides whether it is on sale there (the eggless-brownie box)."""
    w = world
    web_only = Product(
        name="Web-only box", slug=f"{MARKER}-{_tag()}", sales_channels=["web"]
    )
    db.add(web_only)
    await db.flush()
    await recipe_service.draft_and_activate(
        db,
        kind="product",
        owner_id=web_only.id,
        lines=[RecipeLineInput(item_id=w["cake"].id, quantity=Decimal("9"))],
        user_id=w["user"].id,
    )
    await _stock(db, w, w["cake"], "5")
    await db.commit()
    changes = _changes(await auto.tick(db, full=True))
    assert ("Web-only box", False, availability.REASON_STOCK_DEPLETED) in changes
    assert ("Kunafa", False, availability.REASON_STOCK_DEPLETED) not in changes


async def test_a_staff_stockout_is_never_put_back_by_the_system(db, world):
    w = world
    await availability.set_product_stock(
        db,
        branch=w["branch"],
        product_id=w["kunafa"].id,
        in_stock=False,
        actor=w["user"],
    )
    await _stock(db, w, w["cake"], "5")
    await db.commit()
    assert _changes(await auto.tick(db, full=True)) == set()
    row = await _product_row(db, w, w["kunafa"])
    assert (row.is_in_stock, row.unavailable_source) == (False, "staff")


async def test_an_auto_write_on_a_stale_read_never_takes_a_staff_row(db, world):
    """The evaluator decided on a read taken before a cashier's 86 landed:
    the writer re-checks the locked row and leaves the person's call alone."""
    w = world
    await availability.set_product_stock(
        db,
        branch=w["branch"],
        product_id=w["kunafa"].id,
        in_stock=False,
        actor=w["user"],
    )
    await db.commit()
    written = await availability.set_product_stock(
        db,
        branch=w["branch"],
        product_id=w["kunafa"].id,
        in_stock=False,
        actor=availability.SYSTEM_ACTOR,
        source=availability.SOURCE_AUTO,
        auto_state={"items": []},
    )
    assert written is None
    row = await _product_row(db, w, w["kunafa"])
    assert (row.is_in_stock, row.unavailable_source) == (False, "staff")


async def test_switching_the_flag_on_marks_the_branch(db, world):
    w = world
    await db.execute(
        InventoryAvailabilityDirty.__table__.delete().where(
            InventoryAvailabilityDirty.branch_id == w["branch"].id
        )
    )
    await auto.mark_branch_dirty(db, w["branch"].id)
    assert w["cake"].id in await _dirty(db, w["branch"].id)
    assert w["box"].id not in await _dirty(db, w["branch"].id)
