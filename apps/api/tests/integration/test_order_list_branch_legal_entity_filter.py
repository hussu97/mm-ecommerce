"""The admin orders list narrows by branch and by legal entity (multi-select).

Both mirror the dashboard filters: a set of branches, a set of legal entities,
each the OR of its members, and the two AND together. Against a real Postgres so
the `IN` clauses run in SQL exactly as the list endpoint issues them.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.branch import Branch
from app.models.inventory import Warehouse
from app.models.legal_entity import LegalEntity
from app.models.order import DeliveryMethodEnum, Order, OrderStatusEnum
from app.services.orders import order_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-branch-le-filter"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


def _branch():
    return Branch(name=f"{MARKER} b", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}")


def _legal_entity():
    tag = uuid.uuid4().hex[:8]
    return LegalEntity(
        reference=f"{MARKER}-{tag}",
        legal_name=f"{MARKER} legal {tag}",
        brand_name=f"{MARKER} brand {tag}",
        vat_registered=True,
        invoice_title="Tax Invoice",
    )


def _order(*, branch_id, legal_entity_id, number):
    return Order(
        order_number=number,
        email="pytest-ble@example.com",
        source="cashier",
        is_pos=True,
        pos_status="active",
        branch_id=branch_id,
        legal_entity_id=legal_entity_id,
        status=OrderStatusEnum.CREATED,
        delivery_method=DeliveryMethodEnum.PICKUP,
        subtotal=Decimal("10.00"),
        total=Decimal("10.00"),
    )


async def _seed(db):
    """Two branches × two legal entities, one order in each of the four cells."""
    b1, b2 = _branch(), _branch()
    e1, e2 = _legal_entity(), _legal_entity()
    db.add_all([b1, b2, e1, e2])
    await db.flush()
    # An active branch must have exactly one default stock container (DB trigger).
    db.add_all(
        [
            Warehouse(branch_id=b1.id, name="Default", is_default=True),
            Warehouse(branch_id=b2.id, name="Default", is_default=True),
        ]
    )
    await db.flush()
    cells = {}
    for bi, b in (("b1", b1), ("b2", b2)):
        for ei, e in (("e1", e1), ("e2", e2)):
            order = _order(
                branch_id=b.id,
                legal_entity_id=e.id,
                number=f"BLE-{uuid.uuid4().hex[:12]}",
            )
            db.add(order)
            cells[f"{bi}_{ei}"] = order
    await db.flush()
    await db.commit()
    return {
        "b1": b1.id,
        "b2": b2.id,
        "e1": e1.id,
        "e2": e2.id,
        "numbers": {k: v.order_number for k, v in cells.items()},
    }


async def _list(db, **kwargs):
    items, _total = await order_service.get_all_admin(db, per_page=2000, **kwargs)
    return {o.order_number for o in items}


async def test_branch_ids_narrows_to_the_selected_branches(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        env = await _seed(db)
    async with Session() as db:
        got = await _list(db, branch_ids=[env["b1"]])
    nums = env["numbers"]
    assert nums["b1_e1"] in got and nums["b1_e2"] in got
    assert nums["b2_e1"] not in got and nums["b2_e2"] not in got


async def test_legal_entity_ids_narrows_to_the_selected_entities(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        env = await _seed(db)
    async with Session() as db:
        got = await _list(db, legal_entity_ids=[env["e2"]])
    nums = env["numbers"]
    assert nums["b1_e2"] in got and nums["b2_e2"] in got
    assert nums["b1_e1"] not in got and nums["b2_e1"] not in got


async def test_branch_and_legal_entity_and_together(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        env = await _seed(db)
    async with Session() as db:
        got = await _list(db, branch_ids=[env["b1"]], legal_entity_ids=[env["e1"]])
    nums = env["numbers"]
    # Exactly the one cell where both selections meet.
    assert nums["b1_e1"] in got
    for other in ("b1_e2", "b2_e1", "b2_e2"):
        assert nums[other] not in got
