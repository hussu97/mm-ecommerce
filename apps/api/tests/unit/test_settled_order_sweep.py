"""The safety net that closes a paid-but-open counter check.

A register's pay sequence records the tender and then closes the order in two
separate calls; if the connection drops between them the sale is left at `created`
with a zero balance and no receipt (POS-K001-2026-09-19-0063). The sweeper finds
those and closes them through the ordinary `close_order` path, attributed to the
cashier who took the payment. These mock the DB so the selection and per-order
guard logic run without a Postgres — the SQL itself is exercised at the deploy
gate.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import ConflictError
from app.services.pos import pos_order_service, settled_order_service

pytestmark = pytest.mark.asyncio


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _order(*, balance="0.00", status="created", pos_status="active", source="cashier"):
    payer = uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid4(),
        order_number=f"POS-K001-2026-09-19-{uuid.uuid4().hex[:4]}",
        source=source,
        status=status,
        pos_status=pos_status,
        balance_due=Decimal(balance),
        payments=[SimpleNamespace(user_id=payer, is_refund=False)],
        _payer=payer,
    )


def _db_with_candidates(orders):
    """A db whose candidate query yields `orders`' ids and whose `get` returns a
    user for any id."""
    db = SimpleNamespace()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [o.id for o in orders]
    db.execute = AsyncMock(return_value=result)
    db.get = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4(), email="k@x.io"))
    db.begin_nested = MagicMock(return_value=_Nested())
    return db


@pytest.fixture
def _get_order_returns(monkeypatch):
    def _install(orders):
        by_id = {o.id: o for o in orders}
        monkeypatch.setattr(
            pos_order_service,
            "get_order",
            AsyncMock(side_effect=lambda db, oid: by_id[oid]),
        )

    return _install


async def test_closes_a_settled_open_check(_get_order_returns, monkeypatch):
    order = _order(balance="0.00")
    _get_order_returns([order])
    close = AsyncMock()
    monkeypatch.setattr(pos_order_service, "close_order", close)
    db = _db_with_candidates([order])

    closed = await settled_order_service.sweep_settled_open_orders(db)

    assert closed == [order.order_number]
    close.assert_awaited_once()
    # Closed on behalf of the cashier who took the payment.
    db.get.assert_awaited_once_with(pos_order_service.User, order._payer)


async def test_skips_a_check_that_still_owes(_get_order_returns, monkeypatch):
    """A candidate whose balance moved back above zero between the query and the
    lock (a partial refund, a re-priced line) is left alone."""
    order = _order(balance="12.00")
    _get_order_returns([order])
    close = AsyncMock()
    monkeypatch.setattr(pos_order_service, "close_order", close)
    db = _db_with_candidates([order])

    closed = await settled_order_service.sweep_settled_open_orders(db)

    assert closed == []
    close.assert_not_awaited()


async def test_skips_one_that_closed_in_the_gap_and_keeps_going(
    _get_order_returns, monkeypatch
):
    """A close that raises (already closed, till closed under it) is skipped
    without aborting the sweep — the next order still closes."""
    gone = _order()
    good = _order()
    _get_order_returns([gone, good])

    async def _close(db, *, order, user):
        if order is gone:
            raise ConflictError("Order is closed and can no longer be modified")

    monkeypatch.setattr(pos_order_service, "close_order", AsyncMock(side_effect=_close))
    db = _db_with_candidates([gone, good])

    closed = await settled_order_service.sweep_settled_open_orders(db)

    assert closed == [good.order_number]
