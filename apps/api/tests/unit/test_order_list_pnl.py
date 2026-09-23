"""
The console's orders list carries the P&L; the customer's own list never does.

The admin screen reads each order's margin from `pnl` on its rows, computed in
SQL by the same `order_pnl.line_columns()` the P&L page sums — so the two cannot
drift. The account list (`get_user_orders`) is the customer's order history,
and what a courier cost us or a marketplace kept is not theirs to see: it used
to carry `net_value`/`cost_cover` too, and these tests keep it from coming back.
"""

from __future__ import annotations

import inspect
import os
import uuid
from decimal import Decimal

import pytest

from app.schemas.order import AdminOrderListResponse, OrderListResponse
from app.services.orders import order_service


def test_the_admin_list_selects_the_pnl_columns():
    source = inspect.getsource(order_service.get_all_admin)
    assert "_pnl_columns()" in source
    assert "_pnl_brief(" in source


def test_the_customer_list_carries_no_margin():
    source = inspect.getsource(order_service.get_user_orders)
    assert "_pnl_columns" not in source
    leaked = {"pnl", "net_value", "cost_cover", "covers_direct_cost"}
    assert not leaked & set(OrderListResponse.model_fields)
    assert "pnl" in AdminOrderListResponse.model_fields


DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

db_required = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)


# ── the Refunds filter counts the money, not the status ───────────────────────
#
# The dashboard's Refunds card counts every order with a `refunded_amount`, but
# the orders-list "Refunded" filter matched `status = 'refunded'` — a status a
# refund almost never leaves the order in (an admin full refund moves it to
# `cancelled`, a partial one leaves it `delivered`, an aggregator refund never
# touches the status). So the card showed refunds the filter could not find.
# This pins the fix: the filter now selects `refunded_amount > 0`.


@db_required
class TestRefundedFilterMatchesTheMoney:
    @pytest.fixture
    async def engine(self):
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(DATABASE_URL)
        yield engine
        await engine.dispose()

    async def test_refunded_filter_finds_refunded_amount_not_status(self, engine):
        from sqlalchemy import delete
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.models import Branch, Order
        from app.models.inventory import Warehouse
        from app.models.order import DeliveryMethodEnum, OrderStatusEnum

        tag = uuid.uuid4().hex[:10]
        Session = async_sessionmaker(engine, expire_on_commit=False)

        def _order(suffix, **over):
            base = dict(
                order_number=f"RFND-{tag}-{suffix}",
                email="rfnd@example.com",
                source="aggregator",
                status=OrderStatusEnum.DELIVERED,
                delivery_method=DeliveryMethodEnum.DELIVERY,
                payment_method="card",
                subtotal=Decimal("100.00"),
                total=Decimal("120.00"),
            )
            base.update(over)
            return Order(**base)

        try:
            async with Session() as db:
                branch = Branch(name=f"rfnd-{tag}", reference=f"rfnd-{tag}")
                db.add(branch)
                await db.flush()
                db.add(Warehouse(branch_id=branch.id, name="D", is_default=True))

                # A delivered order with money refunded — the case the card counts
                # and the old status filter missed.
                refunded = _order("hit", refunded_amount=Decimal("25.00"))
                refunded.branch_id = branch.id
                # A delivered order with nothing refunded — must not appear.
                plain = _order("miss")
                plain.branch_id = branch.id
                db.add_all([refunded, plain])
                await db.commit()
                branch_id = branch.id
                refunded_number = refunded.order_number

            async with Session() as db:
                items, total = await order_service.get_all_admin(
                    db,
                    statuses=[OrderStatusEnum.REFUNDED.value],
                    branch_id=branch_id,
                    per_page=50,
                )
                numbers = {i.order_number for i in items}
                assert total == 1, f"expected 1 refunded order, got {total}"
                assert numbers == {refunded_number}, (
                    "Refunded filter must match refunded_amount, not status: "
                    f"got {numbers}"
                )

            async with Session() as db:
                await db.execute(delete(Order).where(Order.branch_id == branch_id))
                await db.execute(delete(Branch).where(Branch.id == branch_id))
                await db.commit()
        finally:
            await engine.dispose()


# ── the item count leaves out voided lines ────────────────────────────────────
#
# The list's item_count summed every OrderItem.quantity, voids included, so a
# counter ticket where the cashier voided all but one line read as 100+ items
# against a AED 35 total (POS-B001-2026-09-15-0035). The money columns already
# ignore voids; the count now does too — via IS DISTINCT FROM, so the NULL
# status an off-counter line carries still counts.


@db_required
class TestItemCountExcludesVoids:
    @pytest.fixture
    async def engine(self):
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(DATABASE_URL)
        yield engine
        await engine.dispose()

    async def test_voided_lines_are_not_counted_but_null_status_is(self, engine):
        from sqlalchemy import delete, select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.models import Branch, Order, OrderItem
        from app.models.inventory import Warehouse
        from app.models.order import (
            DeliveryMethodEnum,
            OrderItemStatusEnum,
            OrderStatusEnum,
        )

        tag = uuid.uuid4().hex[:10]
        Session = async_sessionmaker(engine, expire_on_commit=False)

        def _item(order_id, name, status):
            return OrderItem(
                order_id=order_id,
                product_name=name,
                product_sku=f"SKU-{name.replace(' ', '')}",
                quantity=1,
                unit_price=Decimal("35.00"),
                base_price=Decimal("35.00"),
                total_price=Decimal("35.00"),
                status=status,
            )

        try:
            async with Session() as db:
                branch = Branch(name=f"cnt-{tag}", reference=f"cnt-{tag}")
                db.add(branch)
                await db.flush()
                db.add(Warehouse(branch_id=branch.id, name="D", is_default=True))

                # A counter ticket: one line closed, one voided.
                counter = Order(
                    order_number=f"CNT-{tag}-pos",
                    email="cnt@example.com",
                    source="cashier",
                    status=OrderStatusEnum.DELIVERED,
                    delivery_method=DeliveryMethodEnum.PICKUP,
                    payment_method="cash",
                    subtotal=Decimal("35.00"),
                    total=Decimal("35.00"),
                    branch_id=branch.id,
                )
                # A website order whose lines carry no item status (NULL).
                web = Order(
                    order_number=f"CNT-{tag}-web",
                    email="cnt@example.com",
                    source="online",
                    status=OrderStatusEnum.DELIVERED,
                    delivery_method=DeliveryMethodEnum.DELIVERY,
                    payment_method="card",
                    subtotal=Decimal("70.00"),
                    total=Decimal("70.00"),
                    branch_id=branch.id,
                )
                db.add_all([counter, web])
                await db.flush()
                db.add_all(
                    [
                        _item(
                            counter.id, "Sold Slice", OrderItemStatusEnum.CLOSED.value
                        ),
                        _item(
                            counter.id, "Voided Slice", OrderItemStatusEnum.VOID.value
                        ),
                        _item(web.id, "Web Slice A", None),
                        _item(web.id, "Web Slice B", None),
                    ]
                )
                await db.commit()
                branch_id = branch.id
                counter_number, web_number = counter.order_number, web.order_number

            async with Session() as db:
                items, _ = await order_service.get_all_admin(
                    db, branch_id=branch_id, per_page=50
                )
                counts = {i.order_number: i.item_count for i in items}
                # The void line is dropped: one closed line, not two.
                assert counts[counter_number] == 1, (
                    f"voided line counted: {counts[counter_number]}"
                )
                # NULL-status lines still count.
                assert counts[web_number] == 2, (
                    f"NULL-status lines dropped: {counts[web_number]}"
                )

            async with Session() as db:
                await db.execute(
                    delete(OrderItem).where(
                        OrderItem.order_id.in_(
                            select(Order.id).where(Order.branch_id == branch_id)
                        )
                    )
                )
                await db.execute(delete(Order).where(Order.branch_id == branch_id))
                await db.execute(delete(Branch).where(Branch.id == branch_id))
                await db.commit()
        finally:
            await engine.dispose()
