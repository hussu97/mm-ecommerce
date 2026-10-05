"""`PUT /orders/{n}/delivery/courier-cost`: a third party's charge, entered by hand.

Only on a delivered third-party order; stored VAT inclusive; what the P&L and
the delivery panel then read as the courier cost.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.branch import Branch
from app.models.inventory import Warehouse
from app.models.order import DeliveryMethodEnum, Order, OrderStatusEnum
from app.models.order_delivery import OrderDelivery

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

D = Decimal
AT = datetime(2031, 2, 3, 8, 0, tzinfo=timezone.utc)


class _Admin:
    def __init__(self):
        self.id = uuid.uuid4()
        self.email = "courier-cost@example.com"
        self.is_admin = True

    def can(self, _permission: str) -> bool:
        return True


@pytest.fixture
async def world():
    engine = create_async_engine(DATABASE_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:8]
    async with maker() as db:
        branch = Branch(name=f"tp-cost {tag}", reference=f"tpc-{tag}")
        db.add(branch)
        await db.flush()
        db.add(Warehouse(branch_id=branch.id, name="Default", is_default=True))

        def order(suffix, status, delivered):
            return Order(
                order_number=f"TPC-{tag}-{suffix}",
                email="tp@example.com",
                branch_id=branch.id,
                source="online",
                status=status,
                delivery_method=DeliveryMethodEnum.DELIVERY,
                subtotal=D("70.00"),
                delivery_fee=D("80.00"),
                total=D("150.00"),
                vat_rate=D("0.05"),
                vat_amount=D("7.14"),
                total_excl_vat=D("142.86"),
                payment_method="card",
                created_at=AT,
                delivered_at=AT if delivered else None,
            )

        orders = {
            "delivered": order("D", OrderStatusEnum.DELIVERED, True),
            "packed": order("P", OrderStatusEnum.PACKED, False),
            "lalamove": order("L", OrderStatusEnum.DELIVERED, True),
        }
        db.add_all(orders.values())
        await db.flush()
        for key, provider in (
            ("delivered", "third_party"),
            ("packed", "third_party"),
            ("lalamove", "lalamove"),
        ):
            db.add(
                OrderDelivery(
                    order_id=orders[key].id,
                    provider=provider,
                    zone_name="Sharjah · Al Dhaid",
                    fee_charged=D("80.00"),
                    quoted_cost=D("49.00"),
                )
            )
        await db.commit()

    from app.core.deps import get_current_active_user, get_db
    from app.main import app

    async def override_get_db():
        async with maker() as session:
            yield session
            await session.commit()

    async def override_admin():
        return _Admin()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_active_user] = override_admin
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield {"client": client, "orders": orders, "maker": maker}
    finally:
        app.dependency_overrides.clear()
        async with maker() as db:
            ids = [o.id for o in orders.values()]
            await db.execute(
                OrderDelivery.__table__.delete().where(OrderDelivery.order_id.in_(ids))
            )
            await db.execute(Order.__table__.delete().where(Order.id.in_(ids)))
            await db.execute(
                Warehouse.__table__.delete().where(Warehouse.branch_id == branch.id)
            )
            await db.execute(Branch.__table__.delete().where(Branch.id == branch.id))
            await db.commit()
        await engine.dispose()


def _url(order: Order) -> str:
    return f"/api/v1/orders/{order.order_number}/delivery"


async def _stored(world, key) -> Decimal | None:
    async with world["maker"]() as db:
        return await db.scalar(
            select(OrderDelivery.cost_total).where(
                OrderDelivery.order_id == world["orders"][key].id
            )
        )


async def test_a_delivered_third_party_order_shows_no_cost_until_one_is_entered(world):
    response = await world["client"].get(_url(world["orders"]["delivered"]))
    assert response.status_code == 200, response.text
    body = response.json()
    # The 49.00 is its checkout quote, not a cost.
    assert body["quoted_cost"] == 49.0
    assert body["courier_cost"] is None
    assert body["courier_cost_editable"] is True
    assert body["margin"] is None


async def test_the_entered_cost_is_stored_vat_inclusive_and_read_back(world):
    client, order = world["client"], world["orders"]["delivered"]
    response = await client.put(f"{_url(order)}/courier-cost", json={"cost": "35.00"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["courier_cost"] == 35.0
    assert body["margin"] == 45.0  # 80 charged − 35
    assert await _stored(world, "delivered") == D("35.00")

    response = await client.put(
        f"{_url(order)}/courier-cost", json={"cost": "100", "vat_inclusive": False}
    )
    assert response.json()["courier_cost"] == 105.0

    response = await client.put(f"{_url(order)}/courier-cost", json={"cost": None})
    assert response.json()["courier_cost"] is None
    assert await _stored(world, "delivered") is None


async def test_an_undelivered_or_integrated_order_refuses_a_hand_entered_cost(world):
    client = world["client"]
    for key in ("packed", "lalamove"):
        order = world["orders"][key]
        response = await client.put(
            f"{_url(order)}/courier-cost", json={"cost": "35.00"}
        )
        assert response.status_code == 409, (key, response.text)
        assert (await client.get(_url(order))).json()["courier_cost_editable"] is False
    # The integrated courier keeps its quote as the cost until it invoices.
    lalamove = (await client.get(_url(world["orders"]["lalamove"]))).json()
    assert lalamove["courier_cost"] == 49.0


async def test_a_negative_cost_is_refused(world):
    order = world["orders"]["delivered"]
    response = await world["client"].put(
        f"{_url(order)}/courier-cost", json={"cost": "-5"}
    )
    assert response.status_code == 422
