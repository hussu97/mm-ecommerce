"""The ledger endpoint searches and pages server-side (F-ADM-2).

It used to return the first 100 and the admin searched over just those — so
`search` now reaches the whole log by reference OR item name/SKU, and `offset`
pages it. Exercises `list_transactions` directly (no HTTP/auth) against real rows.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.inventory import list_transactions
from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryTransaction,
    InventoryTransactionItem,
    Warehouse,
)

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-txn-search"
ADMIN = SimpleNamespace(is_admin=True, role=None, id=uuid.uuid4())


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def world(engine):
    """A branch, two named items, three transactions across them."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} b", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
        )
        db.add(branch)
        await db.flush()
        db.add(Warehouse(branch_id=branch.id, name="Default", is_default=True))

        def _item(name):
            return InventoryItem(
                sku=f"{MARKER}-{uuid.uuid4().hex[:8]}",
                name=name,
                kind="raw_material",
                tracking_mode="stocked",
                storage_unit="g",
                ingredient_unit="g",
                storage_to_ingredient_factor=Decimal("1"),
                cost=Decimal("1"),
            )

        flour = _item(f"{MARKER} Flour")
        sugar = _item(f"{MARKER} Sugar")
        db.add_all([flour, sugar])
        await db.flush()

        def _txn(ref, item):
            txn = InventoryTransaction(
                reference=ref,
                type="quantity_adjustment",
                status="draft",
                branch_id=branch.id,
                business_date="2026-09-09",
            )
            db.add(txn)
            return txn, item

        pending = [
            _txn(f"{MARKER}-ALPHA", flour),
            _txn(f"{MARKER}-BETA", sugar),
            _txn(f"{MARKER}-GAMMA", flour),
        ]
        await db.flush()
        for txn, item in pending:
            db.add(
                InventoryTransactionItem(
                    transaction_id=txn.id,
                    item_id=item.id,
                    quantity=Decimal("5"),
                    unit="ingredient",
                    conversion_factor=Decimal("1"),
                    unit_cost=Decimal("1"),
                )
            )
        await db.commit()
        branch_id = branch.id

    yield Session, branch_id

    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        await db.execute(
            InventoryTransactionItem.__table__.delete().where(
                InventoryTransactionItem.transaction_id.in_(
                    select(InventoryTransaction.id).where(
                        InventoryTransaction.branch_id == branch_id
                    )
                )
            )
        )
        await db.execute(
            InventoryTransaction.__table__.delete().where(
                InventoryTransaction.branch_id == branch_id
            )
        )
        await db.execute(
            InventoryItem.__table__.delete().where(
                InventoryItem.sku.like(f"{MARKER}-%")
            )
        )
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == branch_id)
        )
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.execute(text("SET session_replication_role = 'origin'"))
        await db.commit()


def _refs(rows):
    return {r.reference for r in rows if (r.reference or "").startswith(MARKER)}


# Called as a plain function, not through FastAPI, so every Query()-defaulted
# param must be passed explicitly (status_filter/limit/offset) — FastAPI is what
# would otherwise resolve those markers to their values.
async def _list(db, **kw):
    kw.setdefault("status_filter", None)
    kw.setdefault("limit", 100)
    kw.setdefault("offset", 0)
    return await list_transactions(db=db, user=ADMIN, **kw)


async def test_search_matches_a_reference(world):
    Session, branch_id = world
    async with Session() as db:
        rows = await _list(db, branch_id=branch_id, search="ALPHA")
    assert _refs(rows) == {f"{MARKER}-ALPHA"}


async def test_search_matches_an_item_name_across_transactions(world):
    Session, branch_id = world
    async with Session() as db:
        rows = await _list(db, branch_id=branch_id, search=f"{MARKER} Flour")
    # Both flour transactions, not the sugar one.
    assert _refs(rows) == {f"{MARKER}-ALPHA", f"{MARKER}-GAMMA"}


async def test_offset_pages_through_without_overlap(world):
    Session, branch_id = world
    async with Session() as db:
        first = await _list(db, branch_id=branch_id, limit=2, offset=0)
        second = await _list(db, branch_id=branch_id, limit=2, offset=2)
    assert len(first) == 2
    assert len(second) == 1
    assert _refs(first).isdisjoint(_refs(second))
    assert _refs(first) | _refs(second) == {
        f"{MARKER}-ALPHA",
        f"{MARKER}-BETA",
        f"{MARKER}-GAMMA",
    }
