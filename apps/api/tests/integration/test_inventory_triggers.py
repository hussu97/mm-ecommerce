"""The two plpgsql immutability triggers, against a real Postgres (F-TST-4).

Migrations 186 and 194 guard the inventory ledger in the database itself:

  * `prevent_closed_inventory_transaction_mutation` — a closed transaction may
    not be edited, and a transaction may only be closed through the ledger poster
    (which sets `mm.inventory_posting = 'on'`).
  * `prevent_closed_inventory_line_mutation` — a line whose parent is closed is
    immutable, with the same posting-marker bypass.

194 existed because the line trigger originally lacked that bypass, so closing a
transaction (which writes the line balances) raised and "no stock ever moved".
These pin all of it: a closed line is immutable, the marker lets the poster write
the movement, and the two triggers agree on the marker.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryTransaction,
    InventoryTransactionItem,
    TransactionStatusEnum,
    Warehouse,
)

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-triggers"
BUSINESS_DATE = "2026-09-07"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def branch_and_item(session_factory):
    async with session_factory() as db:
        branch = Branch(
            name=f"{MARKER} b", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
        )
        db.add(branch)
        await db.flush()
        db.add(Warehouse(branch_id=branch.id, name="Default", is_default=True))
        item = InventoryItem(
            sku=f"{MARKER}-{uuid.uuid4().hex[:10]}",
            name="Flour",
            kind="raw_material",
            tracking_mode="stocked",
            storage_unit="g",
            ingredient_unit="g",
            storage_to_ingredient_factor=Decimal("1"),
            cost=Decimal("1"),
        )
        db.add(item)
        await db.commit()
        ids = (branch.id, item.id)
    yield ids
    branch_id, item_id = ids
    async with session_factory() as db:
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
            InventoryItem.__table__.delete().where(InventoryItem.id == item_id)
        )
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == branch_id)
        )
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.execute(text("SET session_replication_role = 'origin'"))
        await db.commit()


async def _make_draft(session_factory, branch_id, item_id):
    """A DRAFT transaction with one line — insertable, since the parent is open."""
    async with session_factory() as db:
        txn = InventoryTransaction(
            reference=f"{MARKER}-{uuid.uuid4().hex[:10]}",
            type="quantity_adjustment",
            status=TransactionStatusEnum.DRAFT.value,
            branch_id=branch_id,
            business_date=BUSINESS_DATE,
        )
        db.add(txn)
        await db.flush()
        line = InventoryTransactionItem(
            transaction_id=txn.id,
            item_id=item_id,
            quantity=Decimal("5"),
            unit="ingredient",
            conversion_factor=Decimal("1"),
            unit_cost=Decimal("1"),
        )
        db.add(line)
        await db.commit()
        return txn.id, line.id


async def _close_with_marker(session_factory, txn_id):
    """Close a transaction the way the ledger poster does: marker set, then write."""
    async with session_factory() as db:
        await db.execute(text("SET LOCAL mm.inventory_posting = 'on'"))
        await db.execute(
            text(
                "UPDATE inventory_transactions SET status = 'closed', "
                "posting_sequence = 1, posted_at = now() WHERE id = :i"
            ),
            {"i": txn_id},
        )
        await db.commit()


async def test_a_closed_transaction_line_is_immutable(session_factory, branch_and_item):
    branch_id, item_id = branch_and_item
    txn_id, line_id = await _make_draft(session_factory, branch_id, item_id)
    await _close_with_marker(session_factory, txn_id)

    # No marker: the line under a closed transaction cannot be touched.
    with pytest.raises(Exception) as excinfo:
        async with session_factory() as db:
            await db.execute(
                text(
                    "UPDATE inventory_transaction_items SET quantity = 9 WHERE id = :i"
                ),
                {"i": line_id},
            )
            await db.commit()
    assert "closed inventory transaction lines are immutable" in str(excinfo.value)

    with pytest.raises(Exception) as excinfo:
        async with session_factory() as db:
            await db.execute(
                text("DELETE FROM inventory_transaction_items WHERE id = :i"),
                {"i": line_id},
            )
            await db.commit()
    assert "closed inventory transaction lines are immutable" in str(excinfo.value)


async def test_the_posting_marker_allows_the_movement(session_factory, branch_and_item):
    branch_id, item_id = branch_and_item
    txn_id, line_id = await _make_draft(session_factory, branch_id, item_id)

    # The poster's own path: with the marker set, closing the transaction AND
    # writing the line's balances in the same transaction both succeed. This is
    # the exact combination migration 194 unblocked — both triggers honour the
    # marker, so they agree.
    async with session_factory() as db:
        await db.execute(text("SET LOCAL mm.inventory_posting = 'on'"))
        await db.execute(
            text(
                "UPDATE inventory_transactions SET status = 'closed', "
                "posting_sequence = 1, posted_at = now() WHERE id = :i"
            ),
            {"i": txn_id},
        )
        await db.execute(
            text(
                "UPDATE inventory_transaction_items "
                "SET balance_after_quantity = 5, signed_quantity = 5 WHERE id = :i"
            ),
            {"i": line_id},
        )
        await db.commit()

    async with session_factory() as db:
        txn = await db.get(InventoryTransaction, txn_id)
        line = await db.get(InventoryTransactionItem, line_id)
        assert txn.status == TransactionStatusEnum.CLOSED.value
        assert Decimal(str(line.balance_after_quantity)) == Decimal("5")


async def test_closing_without_the_marker_is_refused(session_factory, branch_and_item):
    branch_id, item_id = branch_and_item
    txn_id, _ = await _make_draft(session_factory, branch_id, item_id)

    # The transaction trigger: a draft may only be closed through the poster.
    with pytest.raises(Exception) as excinfo:
        async with session_factory() as db:
            await db.execute(
                text(
                    "UPDATE inventory_transactions SET status = 'closed' WHERE id = :i"
                ),
                {"i": txn_id},
            )
            await db.commit()
    assert "must be closed through the ledger poster" in str(excinfo.value)


async def test_a_closed_transaction_cannot_be_edited(session_factory, branch_and_item):
    branch_id, item_id = branch_and_item
    txn_id, _ = await _make_draft(session_factory, branch_id, item_id)
    await _close_with_marker(session_factory, txn_id)

    # Even with the marker, an already-closed transaction is immutable (the
    # marker only lets the closing write through, not later edits).
    with pytest.raises(Exception) as excinfo:
        async with session_factory() as db:
            await db.execute(
                text("UPDATE inventory_transactions SET notes = 'x' WHERE id = :i"),
                {"i": txn_id},
            )
            await db.commit()
    assert "closed inventory transactions are immutable" in str(excinfo.value)
