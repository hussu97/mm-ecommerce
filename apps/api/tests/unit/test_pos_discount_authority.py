"""
A discount that is not `open` must come from a configured row, not the client.

`pos.discounts.open` is the permission for a typed-in discount; a `predefined`
one is picked from a list and gated more cheaply. Trusting the client's
name/value under the `predefined` label let a cashier type any amount under the
cheaper permission, and a `source="promotion"` row masquerades as engine-managed.
The fix (F-POS-3): for `source != open`, load the `Discount` by `reference_id`
and take its name/kind/value; refuse `promotion` from the till entirely.
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


def _discount(**over):
    fields = dict(
        id=uuid.uuid4(),
        name="Loyalty 10%",
        is_percentage=True,
        amount=Decimal("0.10"),
        is_active=True,
        deleted_at=None,
    )
    fields.update(over)
    return SimpleNamespace(**fields)


def _db(*, discount=None):
    db = SimpleNamespace()
    db.get = AsyncMock(return_value=discount)
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


async def test_predefined_without_a_reference_is_refused():
    with pytest.raises(BadRequestError, match="name the discount"):
        await pos_order_service.apply_discount(
            _db(),
            order=_order(),
            user=SimpleNamespace(id=uuid.uuid4()),
            name="Loyalty",
            is_percentage=True,
            value=Decimal("0.10"),
            source="predefined",
            reference_id=None,
        )


async def test_predefined_with_a_missing_reference_is_refused():
    with pytest.raises(BadRequestError, match="not found"):
        await pos_order_service.apply_discount(
            _db(discount=None),
            order=_order(),
            user=SimpleNamespace(id=uuid.uuid4()),
            name="Loyalty",
            is_percentage=True,
            value=Decimal("0.10"),
            source="predefined",
            reference_id=uuid.uuid4(),
        )


async def test_predefined_takes_its_value_from_the_configured_row_not_the_client():
    configured = _discount(
        name="Loyalty 10%", is_percentage=True, amount=Decimal("0.10")
    )
    db = _db(discount=configured)

    await pos_order_service.apply_discount(
        db,
        order=_order(),
        user=SimpleNamespace(id=uuid.uuid4()),
        # The client tries to smuggle in a 90% discount under the predefined label.
        name="Totally 90% off",
        is_percentage=True,
        value=Decimal("0.90"),
        source="predefined",
        reference_id=configured.id,
    )

    (added,) = [c.args[0] for c in db.add.call_args_list]
    assert added.name == "Loyalty 10%", "the name came from the configured discount"
    assert added.is_percentage is True
    assert added.value == Decimal("0.10"), "the client's 90% was ignored"
    assert added.source == "predefined"
    assert added.reference_id == configured.id


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
