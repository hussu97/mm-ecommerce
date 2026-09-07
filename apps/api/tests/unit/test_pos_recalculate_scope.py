"""
A register mutation must never re-price an order priced by another channel.

`pos_order_service.recalculate` is the single writer of money and it overwrites
subtotal/discount/charges/VAT/total from the POS line and charge model — which is
right for a counter check and destructive for a website or marketplace order,
whose delivery fee and web promo discount live nowhere in that model. Those
orders sit `pos_status in (pending, active)` once attached, so `_assert_open`
alone lets them through; `_assert_counter_check` is the guard that stops the
re-price (F-POS-1).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import ConflictError
from app.services.pos import pos_order_service


def _online_order():
    """A website order attached to a register: open on the POS, priced by the web."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        source="online",
        is_pos=True,
        pos_status="active",
        # The two figures the register would destroy: the delivery fee and the
        # web promo discount, both carried on the order, neither reconstructable
        # from the POS line/charge model.
        delivery_fee=Decimal("15.00"),
        discount_amount=Decimal("20.00"),
        subtotal=Decimal("100.00"),
        total=Decimal("95.00"),
        order_discounts=[],
        order_charges=[],
        items=[],
    )


class TestCounterCheckGuard:
    def test_cashier_order_passes(self):
        order = SimpleNamespace(source="cashier")
        # Does not raise.
        pos_order_service._assert_counter_check(order)

    @pytest.mark.parametrize("channel", ["online", "aggregator", "api", "call_center"])
    def test_non_cashier_order_is_refused(self, channel):
        order = SimpleNamespace(source=channel)
        with pytest.raises(ConflictError, match="website or marketplace"):
            pos_order_service._assert_counter_check(order)


@pytest.mark.asyncio
async def test_a_website_order_keeps_its_delivery_fee_and_promo_when_a_cashier_touches_it(
    monkeypatch,
):
    order = _online_order()

    # If the guard failed to fire, recalculate would run and overwrite the totals.
    recalc = AsyncMock(side_effect=AssertionError("recalculate must not run"))
    monkeypatch.setattr(pos_order_service, "recalculate", recalc)

    db = SimpleNamespace(get=AsyncMock(), add=lambda *_: None, flush=AsyncMock())

    with pytest.raises(ConflictError, match="website or marketplace"):
        await pos_order_service.add_item(
            db,
            order=order,
            user=SimpleNamespace(id=uuid.uuid4()),
            product_id=uuid.uuid4(),
            quantity=1,
        )

    recalc.assert_not_called()
    # The web figures survive untouched.
    assert order.delivery_fee == Decimal("15.00")
    assert order.discount_amount == Decimal("20.00")
    assert order.total == Decimal("95.00")


@pytest.mark.asyncio
async def test_a_discount_cannot_be_applied_to_a_website_check(monkeypatch):
    order = _online_order()
    monkeypatch.setattr(
        pos_order_service,
        "recalculate",
        AsyncMock(side_effect=AssertionError("recalculate must not run")),
    )
    db = SimpleNamespace(get=AsyncMock(), add=lambda *_: None, flush=AsyncMock())

    with pytest.raises(ConflictError, match="website or marketplace"):
        await pos_order_service.apply_discount(
            db,
            order=order,
            user=SimpleNamespace(id=uuid.uuid4()),
            name="Staff 20%",
            is_percentage=True,
            value=Decimal("0.20"),
        )
