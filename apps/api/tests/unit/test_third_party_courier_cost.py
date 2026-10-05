"""A third party's courier cost: only what a person entered after delivery.

MM-20261003-002 (Sharjah · Al Dhaid, 2026-10-03) went out with a third party and
the P&L booked its 49.00 checkout quote as courier cost — a backup estimate
nobody was ever billed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models.order_delivery import OrderDelivery
from app.schemas.order import ThirdPartyCourierCostUpdate
from app.services.delivery.third_party_cost import (
    third_party_cost_editable,
    third_party_cost_gross,
)


def _delivery(provider="third_party", quoted=None, cost=None) -> OrderDelivery:
    return OrderDelivery(
        provider=provider,
        quoted_cost=Decimal(quoted) if quoted is not None else None,
        cost_total=Decimal(cost) if cost is not None else None,
    )


def test_a_third_partys_quote_is_never_its_cost():
    assert _delivery(quoted="49.00").courier_cost is None


def test_a_third_partys_entered_cost_is_its_cost():
    assert _delivery(quoted="49.00", cost="35.00").courier_cost == Decimal("35.00")


@pytest.mark.parametrize("provider", ["lalamove", "slider_bike", "noon_send"])
def test_an_integrated_courier_still_falls_back_to_its_quote(provider):
    assert _delivery(provider, quoted="24.00").courier_cost == Decimal("24.00")
    assert _delivery(provider, "24.00", "26.50").courier_cost == Decimal("26.50")


def test_the_sql_and_python_rules_are_the_same_rule():
    sql = str(
        select(OrderDelivery.courier_cost).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "CASE WHEN (order_deliveries.provider = 'third_party')" in sql
    assert "THEN order_deliveries.cost_total" in sql
    assert "coalesce(order_deliveries.cost_total, order_deliveries.quoted_cost)" in sql


def _order(delivered=True):
    return SimpleNamespace(
        delivered_at=datetime(2026, 10, 5, 18, 14, tzinfo=timezone.utc)
        if delivered
        else None
    )


def test_only_a_delivered_third_party_order_takes_an_entered_cost():
    assert third_party_cost_editable(_delivery(), _order())
    assert not third_party_cost_editable(_delivery(), _order(delivered=False))
    assert not third_party_cost_editable(_delivery("lalamove"), _order())
    assert not third_party_cost_editable(_delivery(), None)


def test_the_cost_is_stored_vat_inclusive():
    assert third_party_cost_gross(Decimal("35"), True) == Decimal("35.00")
    # A pre-VAT 100 is 105 inclusive.
    assert third_party_cost_gross(Decimal("100"), False) == Decimal("105.00")
    assert third_party_cost_gross(Decimal("33.33"), False) == Decimal("35.00")
    assert third_party_cost_gross(None, False) is None


def test_the_request_is_bounded_and_vat_inclusive_by_default():
    body = ThirdPartyCourierCostUpdate(cost="35.50")
    assert body.cost == Decimal("35.50") and body.vat_inclusive is True
    assert ThirdPartyCourierCostUpdate(cost=None).cost is None
    for bad in ("-1", "10000.01", "1.234"):
        with pytest.raises(ValidationError):
            ThirdPartyCourierCostUpdate(cost=bad)
