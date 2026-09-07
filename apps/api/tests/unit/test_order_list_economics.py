"""
Both order lists carry the direct-cost column, or neither should claim to.

The console's orders screen reads the shop's margins from `cost_cover` /
`covers_direct_cost`, and its response model documents those as "Computed in SQL
by `order_service.get_all_admin`". They were not: `get_user_orders` selected the
economics columns and `get_all_admin` selected only `item_count`, so every row
on the admin screen — the one that number exists for — fell back to the `None`
default and rendered a dash, including on orders well past the bar.

The fix routed both lists through one pair of helpers. These tests pin that:
each list builder must select the economics columns and apply them, so the two
cannot drift apart again without a red test saying so.
"""

from __future__ import annotations

import inspect
import os
import uuid
from decimal import Decimal

import pytest

from app.services.orders import order_economics, order_service


def test_the_admin_list_computes_the_direct_cost_column():
    """The regression itself: the console list must fill the margin column."""
    source = inspect.getsource(order_service.get_all_admin)
    assert "_economics_columns()" in source, (
        "get_all_admin must select net_value/cost_cover, or the console shows a "
        "dash on every row while its schema promises the number"
    )
    assert "_apply_economics(" in source, (
        "the selected columns must be written onto the row"
    )


def test_the_account_list_computes_the_same_column():
    """The list the fix was ported *from* must keep using the shared helper."""
    source = inspect.getsource(order_service.get_user_orders)
    assert "_economics_columns()" in source
    assert "_apply_economics(" in source


# ── the equivalence the docstring has always promised (F-ORD-15) ──────────────
#
# `_net_value_expression`'s docstring says it "mirrors `OrderEconomics.net`
# exactly and is tested against it". It was not: the SQL omitted
# `cancellation_fee` and `marketing_fee`, so the list overstated the net on every
# order that carried one while the per-order screen (`OrderEconomics.net`)
# subtracted them. Both now subtract both, and this is the test the docstring
# promised — the two answers computed over the same rows and compared.

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

db_required = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)


@db_required
class TestSqlNetMatchesTheDataclass:
    @pytest.fixture
    async def engine(self):
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(DATABASE_URL)
        yield engine
        await engine.dispose()

    async def test_the_two_nets_agree_across_the_fee_mix(self, engine):
        from sqlalchemy import delete, select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.core.money import money
        from app.models import Branch, Order, OrderDelivery
        from app.models.inventory import Warehouse
        from app.models.order import DeliveryMethodEnum, OrderStatusEnum

        tag = uuid.uuid4().hex[:10]
        Session = async_sessionmaker(engine, expire_on_commit=False)

        def _order(suffix, **over):
            base = dict(
                order_number=f"NET-{tag}-{suffix}",
                email="net@example.com",
                source="online",
                status=OrderStatusEnum.DELIVERED,
                delivery_method=DeliveryMethodEnum.DELIVERY,
                payment_method="card",
                subtotal=Decimal("100.00"),
                total=Decimal("120.00"),
                delivery_fee=Decimal("20.00"),
                payment_fee=Decimal("3.62"),
            )
            base.update(over)
            return Order(**base)

        try:
            async with Session() as db:
                branch = Branch(name=f"net-{tag}", reference=f"net-{tag}")
                db.add(branch)
                await db.flush()
                db.add(Warehouse(branch_id=branch.id, name="D", is_default=True))

                # A website order with a courier cost and a partial refund.
                web = _order("web", refunded_amount=Decimal("15.00"))
                web.branch_id = branch.id
                # An aggregator order carrying every fee the net subtracts —
                # commission, payment, cancellation and a merchant-funded promo.
                agg = _order(
                    "agg",
                    source="aggregator",
                    aggregator_channel="Keeta 2.0",
                    delivery_method=DeliveryMethodEnum.DELIVERY,
                    aggregator_fee=Decimal("18.00"),
                    payment_fee=Decimal("2.40"),
                    cancellation_fee=Decimal("5.00"),
                    marketing_fee=Decimal("4.00"),
                )
                agg.branch_id = branch.id
                db.add_all([web, agg])
                await db.flush()

                # Only the website order has an MM courier cost of sale.
                db.add(
                    OrderDelivery(
                        order_id=web.id,
                        provider="lalamove",
                        zone_name="Dubai Marina",
                        cost_total=Decimal("11.50"),
                    )
                )
                await db.commit()
                branch_id = branch.id
                web_id, agg_id = web.id, agg.id

            net_value_col, _ = order_service._economics_columns()

            async with Session() as db:
                for oid in (web_id, agg_id):
                    order = await db.get(Order, oid)
                    sql_net = (
                        await db.execute(select(net_value_col).where(Order.id == oid))
                    ).scalar_one()
                    econ = await order_economics.for_order(db, order)
                    assert money(sql_net) == econ.net, (
                        f"SQL net_value and OrderEconomics.net disagree for {oid}: "
                        f"{sql_net} vs {econ.net}"
                    )

            async with Session() as db:
                await db.execute(
                    delete(OrderDelivery).where(OrderDelivery.order_id == web_id)
                )
                await db.execute(delete(Order).where(Order.branch_id == branch_id))
                await db.execute(delete(Branch).where(Branch.id == branch_id))
                await db.commit()
        finally:
            await engine.dispose()
