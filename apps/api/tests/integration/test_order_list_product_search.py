"""The admin orders `search` also matches a line's product name and SKU.

An order surfaces when it holds a line whose `product_name` or `product_sku`
snapshot contains the term (case-insensitive substring), so "brook" or "FG0052"
finds every order that sold it. A voided counter line does not make the order
match. Against a real Postgres so the EXISTS runs in SQL exactly as the list
endpoint issues it.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.branch import Branch
from app.models.inventory import Warehouse
from app.models.order import DeliveryMethodEnum, Order, OrderItem, OrderStatusEnum
from app.models.pos_order import OrderItemStatusEnum
from app.services.orders import order_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-product-search"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


def _order(*, branch_id, number):
    return Order(
        order_number=number,
        email="pytest-product-search@example.com",
        source="cashier",
        is_pos=True,
        pos_status="active",
        branch_id=branch_id,
        status=OrderStatusEnum.CREATED,
        delivery_method=DeliveryMethodEnum.PICKUP,
        subtotal=Decimal("10.00"),
        total=Decimal("10.00"),
    )


def _item(*, name, sku, status=None):
    return OrderItem(
        product_name=name,
        product_sku=sku,
        quantity=1,
        base_price=Decimal("10.00"),
        unit_price=Decimal("10.00"),
        total_price=Decimal("10.00"),
        status=status,
    )


async def _seed(db):
    """Three orders: a Brookies line, a Chocolate Cake line, and a Brookies line
    that was voided at the register."""
    branch = Branch(name=f"{MARKER} b", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}")
    db.add(branch)
    await db.flush()
    # An active branch must have exactly one default stock container (DB trigger).
    db.add(Warehouse(branch_id=branch.id, name="Default", is_default=True))
    await db.flush()

    numbers = {}
    specs = {
        "brookies": _item(name="Brookies", sku="FG0052"),
        "cake": _item(name="Chocolate Fudge Cake", sku="CK0001"),
        "voided": _item(name="Brookies", sku="FG0052", status=OrderItemStatusEnum.VOID),
    }
    for key, item in specs.items():
        order = _order(branch_id=branch.id, number=f"PS-{uuid.uuid4().hex[:12]}")
        order.items.append(item)
        db.add(order)
        numbers[key] = order.order_number
    await db.flush()
    await db.commit()
    return numbers


async def _list(db, **kwargs):
    items, _total = await order_service.get_all_admin(db, per_page=2000, **kwargs)
    return {o.order_number for o in items}


async def test_search_matches_product_name(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        nums = await _seed(db)
    async with Session() as db:
        got = await _list(db, search="brook")
    assert nums["brookies"] in got
    assert nums["cake"] not in got


async def test_search_matches_product_sku(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        nums = await _seed(db)
    async with Session() as db:
        got = await _list(db, search="fg0052")
    assert nums["brookies"] in got
    assert nums["cake"] not in got


async def test_search_ignores_voided_lines(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        nums = await _seed(db)
    async with Session() as db:
        got = await _list(db, search="brook")
    # The order whose only Brookies line was voided must not surface.
    assert nums["voided"] not in got
