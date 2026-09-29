"""
The till applies one kind of discount by hand: an `open` one.

`pos.discounts.open` is the permission for a typed-in discount. Every other
`order_discounts` row is written by the promotion engine (`promotion` — the
auto discount or a coupon selected with `PUT .../coupon`), so a client-sent
`promotion` or `coupon` label would forge a row the engine and the reports read
as auto-managed (F-POS-3). Both are refused. The `predefined` source — a
discount picked from the `discounts` table — went with that table in
`307_drop_dead_tables`; it was never used in production.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import BadRequestError
from app.services.pos import pos_order_service

pytestmark = pytest.mark.asyncio


def _order():
    return SimpleNamespace(
        id=uuid.uuid4(),
        source="cashier",
        pos_status="active",
        order_discounts=[],
    )


def _db():
    db = SimpleNamespace()
    db.get = AsyncMock(return_value=None)
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    return db


@pytest.fixture(autouse=True)
def _no_recalc(monkeypatch):
    # Authority is decided before the order is re-priced; stub the re-price so the
    # tests need no pricing engine or database.
    monkeypatch.setattr(pos_order_service, "recalculate", AsyncMock(return_value=None))


async def test_promotion_source_is_refused_from_the_till():
    with pytest.raises(BadRequestError, match="promotion"):
        await pos_order_service.apply_discount(
            _db(),
            order=_order(),
            user=SimpleNamespace(id=uuid.uuid4()),
            name="Anything",
            is_percentage=True,
            value=Decimal("0.99"),
            source="promotion",
            reference_id=uuid.uuid4(),
        )


async def test_coupon_source_is_refused_from_the_till():
    with pytest.raises(BadRequestError, match="coupon"):
        await pos_order_service.apply_discount(
            _db(),
            order=_order(),
            user=SimpleNamespace(id=uuid.uuid4()),
            name="Anything",
            is_percentage=True,
            value=Decimal("0.99"),
            source="coupon",
            reference_id=uuid.uuid4(),
        )


async def test_an_open_discount_still_takes_the_typed_value():
    db = _db()
    await pos_order_service.apply_discount(
        db,
        order=_order(),
        user=SimpleNamespace(id=uuid.uuid4()),
        name="Manager 25%",
        is_percentage=True,
        value=Decimal("0.25"),
        source="open",
    )
    (added,) = [c.args[0] for c in db.add.call_args_list]
    assert added.source == "open"
    assert added.value == Decimal("0.25")
    db.get.assert_not_called()  # an open discount loads no configured row
