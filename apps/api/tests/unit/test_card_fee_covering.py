"""A card fee billed to the customer covers the processor's fee on itself too."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.money import money
from app.services.orders.order_fees import card_fee_covering

_STRIPE = (Decimal("0.029"), Decimal("1"))


def _processor_fee(charged: Decimal) -> Decimal:
    fraction, fixed = _STRIPE
    return money((charged * fraction + fixed) * Decimal("1.05"))


@pytest.mark.parametrize("goods", ["34.00", "200.00", "1250.00", "0.00", "9999.99"])
def test_the_fee_line_equals_the_fee_on_everything_charged(goods):
    goods = Decimal(goods)
    line = card_fee_covering(goods, *_STRIPE)
    assert abs(_processor_fee(goods + line) - line) <= Decimal("0.01")


def test_a_fee_on_the_goods_alone_would_leave_the_shop_short():
    """The reason for the gross-up: billing only `fee(goods)` under-recovers."""
    goods = Decimal("1000.00")
    naive = _processor_fee(goods)
    assert _processor_fee(goods + naive) > naive
    assert card_fee_covering(goods, *_STRIPE) > naive
