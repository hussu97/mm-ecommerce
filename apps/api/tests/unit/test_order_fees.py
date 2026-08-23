"""
What an order costs us before anybody counts the flour.

The gap this closes: a Talabat order and a website order sat in the same table
looking equally profitable, while one of them had already lost a quarter of its
basket to a commission nothing modelled. These tests pin the arithmetic, and —
more importantly — pin the three-valued behaviour that keeps an unconfigured
rate from being read as a free one.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import order_fees


def _order(**kwargs) -> SimpleNamespace:
    base = dict(
        order_number="MM-20260823-001",
        total=Decimal("100.00"),
        source="online",
        aggregator_channel=None,
        payment_method="card",
        payment_provider="stripe",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _courier(
    commission_percent=None,
    commission_fixed=None,
    payment_fee_percent=None,
    payment_fee_fixed=None,
) -> SimpleNamespace:
    """A `couriers` row, with every rate absent unless the test sets it."""
    return SimpleNamespace(
        commission_percent=(
            Decimal(commission_percent) if commission_percent is not None else None
        ),
        commission_fixed=(
            Decimal(commission_fixed) if commission_fixed is not None else None
        ),
        payment_fee_percent=(
            Decimal(payment_fee_percent) if payment_fee_percent is not None else None
        ),
        payment_fee_fixed=(
            Decimal(payment_fee_fixed) if payment_fee_fixed is not None else None
        ),
    )


class _FakeDB:
    """A session that answers one `select(...)` with whatever it was handed."""

    def __init__(self, row=None):
        self._row = row

    async def execute(self, _stmt):
        row = self._row
        return SimpleNamespace(scalar_one_or_none=lambda: row)


# ── the marketplace's cut ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_noon_foods_commission_is_the_rate_plus_the_vat_on_it():
    """
    25% of 100 is 25, and the shop pays 5% VAT on the commission invoice.

    The direction matters and is the thing most easily got backwards: the
    order's own VAT is *inclusive* — already inside what the customer paid —
    while a fee billed to the shop is quoted before tax and grossed up.
    """
    courier = _courier(commission_percent="25.00", payment_fee_percent="2.00")
    order = _order(source="aggregator", aggregator_channel="Noon Food")

    fees = await order_fees.compute(_FakeDB(courier), order)

    assert fees.aggregator_fee == Decimal("26.25")  # 25 + 5%
    assert fees.payment_fee == Decimal("2.10")  # 2 + 5%


@pytest.mark.asyncio
async def test_an_unconfigured_rate_is_null_and_never_zero():
    """
    The distinction the whole feature rests on.

    Talabat's rate is not agreed yet. A zero here would tell the shop it keeps
    every dirham of a Talabat order — the exact misreading this module exists to
    prevent — so an absent rate yields an absent fee, and every screen
    downstream says "not itemised".
    """
    courier = _courier()
    order = _order(source="aggregator", aggregator_channel="Talabat")

    fees = await order_fees.compute(_FakeDB(courier), order)

    assert fees.aggregator_fee is None
    assert fees.payment_fee is None


@pytest.mark.asyncio
async def test_an_unknown_channel_is_unknown_rather_than_free():
    """A marketplace we have no `couriers` row for. Same rule, louder cause."""
    order = _order(source="aggregator", aggregator_channel="Some New App")

    fees = await order_fees.compute(_FakeDB(None), order)

    assert fees.aggregator_fee is None
    assert fees.payment_fee is None


@pytest.mark.asyncio
async def test_the_channel_name_is_matched_the_way_the_badge_matches_it():
    """
    `aggregator_channel` holds GrubOps' display name, not our code.

    "Keeta 2.0" has to find the `keeta` row, or every Keeta order prices itself
    at nothing. Resolved through `courier_catalog`, so the fee and the logo can
    never disagree about which marketplace an order came from.
    """
    courier = _courier(commission_percent="30.00")
    order = _order(source="aggregator", aggregator_channel="Keeta 2.0")

    fees = await order_fees.compute(_FakeDB(courier), order)

    assert fees.aggregator_fee == Decimal("31.50")


# ── our own channels ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_card_order_pays_the_gateways_published_rate():
    """2.9% of 100 + 1 = 3.90 before tax, 4.10 after it."""
    gateway = SimpleNamespace(fee_percent=Decimal("2.9"), fee_fixed=Decimal("1"))

    fees = await order_fees.compute(_FakeDB(gateway), _order())

    assert fees.payment_fee == Decimal("4.10")
    assert fees.aggregator_fee is None
    assert fees.payment_fee_is_estimated is True


@pytest.mark.asyncio
async def test_a_cash_order_pays_no_processor_and_that_is_a_real_zero():
    """
    The money is in a drawer. Zero, not null — we know exactly what it cost.

    Charging a counter sale a Stripe fee would make every one of them look worse
    than it is, which is a different failure from the one above and needs the
    opposite answer.
    """
    fees = await order_fees.compute(_FakeDB(None), _order(payment_method="cash"))

    assert fees.payment_fee == Decimal("0")
    assert fees.payment_fee_is_estimated is False


@pytest.mark.asyncio
async def test_a_missing_gateway_row_falls_back_rather_than_charging_nothing():
    """A seeding gap is not a free transaction."""
    fees = await order_fees.compute(_FakeDB(None), _order())

    assert fees.payment_fee == Decimal("4.10")


@pytest.mark.asyncio
async def test_a_zero_total_order_costs_nothing_to_take():
    """A full-discount staff order. Nobody takes a cut of nothing."""
    fees = await order_fees.compute(_FakeDB(None), _order(total=Decimal("0.00")))

    assert fees.payment_fee == Decimal("0")


# ── a fee is a pair, not a number ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_commission_can_carry_a_flat_amount_as_well_as_a_share():
    """
    "25% plus two dirhams an order", which is how several of these are written.

    25 + 2 = 27 before tax, 28.35 after it. A percentage-only column dropped the
    flat half of every such contract silently — the order still looked priced.
    """
    courier = _courier(commission_percent="25.00", commission_fixed="2.00")
    order = _order(source="aggregator", aggregator_channel="Noon Food")

    fees = await order_fees.compute(_FakeDB(courier), order)

    assert fees.aggregator_fee == Decimal("28.35")


@pytest.mark.asyncio
async def test_a_flat_only_contract_needs_no_percentage():
    """A fixed 5 an order, with no share of the basket. 5 + VAT."""
    courier = _courier(commission_fixed="5.00")
    order = _order(source="aggregator", aggregator_channel="Deliveroo")

    fees = await order_fees.compute(_FakeDB(courier), order)

    assert fees.aggregator_fee == Decimal("5.25")


@pytest.mark.asyncio
async def test_only_both_halves_being_absent_means_unknown():
    """
    The rule that decides whether a channel can be costed at all.

    A contract quoting only a percentage leaves the flat column null, and that
    null is genuinely nothing rather than a gap. Treating either absent half as
    "unknown" would blank the net on every channel with a simple contract.
    """
    order = _order(source="aggregator", aggregator_channel="Talabat")

    only_percent = await order_fees.compute(
        _FakeDB(_courier(commission_percent="15.00")), order
    )
    assert only_percent.aggregator_fee == Decimal("15.75")

    neither = await order_fees.compute(_FakeDB(_courier()), order)
    assert neither.aggregator_fee is None
