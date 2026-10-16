"""Pay-later checks against a real Postgres: the "Sent to kitchen" tab's query,
and the void guard that keeps a part-paid check honest.

A check is fired, set aside, edited and paid later. Two server-side facts make
that safe, and both need real rows:

- `GET /pos/orders?in_kitchen=true` lists open counter checks with food in the
  kitchen — and only those — whatever business day they were opened on;
- voiding a line is refused when a payment already covers more than the check
  would then cost, instead of leaving the shop holding money for food it is not
  serving (which the settled-order sweep would then close as fully paid).
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1 import pos_orders
from app.core.exceptions import ConflictError
from app.models.branch import Branch
from app.models.till import Till
from app.models.user import User
from app.services.pos import pos_order_service

from ._counter_world import build_world

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]


@pytest.fixture
async def Session():
    engine = create_async_engine(DATABASE_URL)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _check(db, world, *, lines: int, fire: bool):
    branch = await db.get(Branch, world.branch_id)
    cashier = await db.get(User, world.cashier_id)
    till = await db.get(Till, world.till_id)
    order = await pos_order_service.open_order(
        db, branch=branch, user=cashier, till=till
    )
    for _ in range(lines):
        await pos_order_service.add_item(
            db, order=order, user=cashier, product_id=world.brownie_id
        )
    order = await pos_order_service.get_order(db, order.id)
    if fire:
        await pos_order_service.send_to_kitchen(db, order=order)
        order = await pos_order_service.get_order(db, order.id)
    return order, cashier, till


async def _in_kitchen(db, branch_id) -> set:
    rows = await pos_orders.list_orders(
        branch_id=branch_id,
        business_date=None,
        pos_status=None,
        order_type=None,
        open_only=False,
        returns_pending=False,
        in_kitchen=True,
        q=None,
        limit=200,
        offset=0,
        db=db,
        _=None,
    )
    return {row.id for row in rows}


async def test_the_tab_lists_fired_open_checks_only(Session):
    async with Session() as db:
        world = await build_world(db)
        fired, cashier, till = await _check(db, world, lines=1, fire=True)
        unfired, _, _ = await _check(db, world, lines=1, fire=False)
        paid, _, _ = await _check(db, world, lines=1, fire=True)
        await pos_order_service.record_payment(
            db,
            order=paid,
            user=cashier,
            payment_method_id=world.card_id,
            amount=Decimal(str(paid.total)),
            till=till,
        )
        await pos_order_service.close_order(
            db, order=await pos_order_service.get_order(db, paid.id), user=cashier
        )
        await db.flush()

        listed = await _in_kitchen(db, world.branch_id)
        await db.rollback()

    assert fired.id in listed
    assert unfired.id not in listed  # still being built at the till
    assert paid.id not in listed  # settled and closed


async def test_editing_a_fired_check_keeps_it_in_the_tab(Session):
    async with Session() as db:
        world = await build_world(db)
        order, cashier, _ = await _check(db, world, lines=1, fire=True)
        # More food after the first round: the new line is unsent until fired.
        await pos_order_service.add_item(
            db, order=order, user=cashier, product_id=world.cookie_id
        )
        order = await pos_order_service.get_order(db, order.id)
        unsent = [i for i in order.items if i.sent_to_kitchen_at is None]
        assert len(unsent) == 1
        listed = await _in_kitchen(db, world.branch_id)
        await db.rollback()
    assert order.id in listed


async def test_voiding_below_what_was_paid_is_refused(Session):
    async with Session() as db:
        world = await build_world(db)
        order, cashier, till = await _check(db, world, lines=2, fire=True)
        one_line = Decimal(str(order.items[0].total_price))
        # The guest pays for both brownies up front…
        await pos_order_service.record_payment(
            db,
            order=order,
            user=cashier,
            payment_method_id=world.card_id,
            amount=Decimal(str(order.total)),
            till=till,
        )
        order = await pos_order_service.get_order(db, order.id)
        # …then one is voided: the money for it is still held.
        with pytest.raises(ConflictError, match="Refund the payment"):
            await pos_order_service.void_item(
                db, order=order, item_id=order.items[0].id, user=cashier
            )
        await db.rollback()
    assert one_line > 0


async def test_voiding_a_line_nobody_has_paid_for_is_fine(Session):
    async with Session() as db:
        world = await build_world(db)
        order, cashier, till = await _check(db, world, lines=2, fire=True)
        line = Decimal(str(order.items[0].total_price))
        # Part-paid: exactly one brownie's worth.
        await pos_order_service.record_payment(
            db,
            order=order,
            user=cashier,
            payment_method_id=world.card_id,
            amount=line,
            till=till,
        )
        order = await pos_order_service.get_order(db, order.id)
        order = await pos_order_service.void_item(
            db, order=order, item_id=order.items[0].id, user=cashier
        )
        assert Decimal(str(order.total)) == line
        await db.rollback()
