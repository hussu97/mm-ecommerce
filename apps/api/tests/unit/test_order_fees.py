"""
What an order costs us before anybody counts the flour.

The gap this closes: a Talabat order and a website order sat in the same table
looking equally profitable, while one of them had already lost a quarter of its
basket to a commission nothing modelled. These tests pin the arithmetic, and —
more importantly — pin the three-valued behaviour that keeps an unconfigured
rate from being read as a free one.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.orders import order_fees


def _order(**kwargs) -> SimpleNamespace:
    base = dict(
        order_number="MM-20260823-001",
        total=Decimal("100.00"),
        source="online",
        aggregator_channel=None,
        payment_method="card",
        payment_provider="stripe",
        branch_id=None,
        aggregator_payment_type=None,
        aggregator_customer_is_member=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _courier(
    commission_percent=None,
    commission_fixed=None,
    payment_fee_percent=None,
    payment_fee_fixed=None,
    *,
    commission_vat_inclusive=False,
    payment_fee_vat_inclusive=False,
    commission_fixed_net_of_base=False,
    payment_fee_cash_exempt=False,
    commission_fixed_requires_member=False,
) -> SimpleNamespace:
    """A `couriers` row, with every rate absent and every flag off unless set."""
    return SimpleNamespace(
        id=uuid.uuid4(),
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
        commission_vat_inclusive=commission_vat_inclusive,
        payment_fee_vat_inclusive=payment_fee_vat_inclusive,
        commission_fixed_net_of_base=commission_fixed_net_of_base,
        payment_fee_cash_exempt=payment_fee_cash_exempt,
        commission_fixed_requires_member=commission_fixed_requires_member,
    )


class _FakeDB:
    """A session that returns the single row it was handed for any `select`.

    Only the own-channel card path reads a table now (the payment gateway) — the
    aggregator rate engine that once read the courier + its branch override is
    gone — so one row answers every query."""

    def __init__(self, row=None):
        self._row = row

    async def execute(self, stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: self._row)

    async def flush(self):  # `stamp` flushes; the fake just accepts it
        return None


# ── the marketplace's cut ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_compute_never_models_a_marketplace_fee():
    """A marketplace order's fee comes ONLY from its scraped statement (stamped
    directly by `stamp`), never from a configured rate. `compute` models nothing
    for it — even handed a fully-populated courier row it returns nulls, because
    the static aggregator rate engine was removed."""
    courier = _courier(commission_percent="25.00", payment_fee_percent="2.00")
    order = _order(source="aggregator", aggregator_channel="Noon Food")

    fees = await order_fees.compute(_FakeDB(courier), order)

    assert fees.aggregator_fee is None
    assert fees.payment_fee is None


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


# ── stamp: a marketplace order's fees come ONLY from scraping ─────────────────


@pytest.mark.asyncio
async def test_stamp_aggregator_writes_only_the_scraped_actuals():
    """The scraped per-order figures land verbatim — no modelled rate involved.

    A `couriers` row is handed to the fake, so if `stamp` ever fell back to
    `compute` it would stamp 26.25 / 2.10 instead; asserting the scraped 9.00 /
    1.50 pins that it does not."""
    order = _order(
        source="aggregator", aggregator_channel="Noon Food", aggregator_fee=None
    )
    courier = _courier(commission_percent="25.00", payment_fee_percent="2.00")

    await order_fees.stamp(
        _FakeDB(courier),
        order,
        actual_commission=Decimal("9.00"),
        actual_payment_fee=Decimal("1.50"),
    )
    assert order.aggregator_fee == Decimal("9.00")
    assert order.payment_fee == Decimal("1.50")


@pytest.mark.asyncio
async def test_stamp_aggregator_leaves_fees_null_until_the_marketplace_settles():
    """No scraped fee yet ⇒ null, NOT a static configured-rate estimate. The
    courier row (25% / 2%) must not be consulted — a null truthfully says "not
    known yet" (some channels, e.g. Careem, settle monthly)."""
    order = _order(
        source="aggregator", aggregator_channel="Noon Food", aggregator_fee=None
    )
    courier = _courier(commission_percent="25.00", payment_fee_percent="2.00")

    await order_fees.stamp(_FakeDB(courier), order)  # no actuals passed
    assert order.aggregator_fee is None
    assert order.payment_fee is None


@pytest.mark.asyncio
async def test_stamp_own_channel_still_models_its_own_card_fee():
    """An own-channel (website) card order keeps its modelled processor fee — that
    fee is genuinely ours, not a marketplace's. Only marketplace fees went
    scrape-only."""
    order = _order(source="online", aggregator_fee=None, payment_fee=None)

    await order_fees.stamp(_FakeDB(row=None), order)  # no gateway row → default 2.9%+1
    assert order.aggregator_fee is None  # no marketplace on an own-channel order
    assert order.payment_fee == Decimal("4.10")  # (100*2.9% + 1) + 5% VAT


@pytest.mark.asyncio
async def test_stamp_propagates_the_scraped_marketing_fee():
    """The merchant-funded promotion the marketplace billed back (Keeta's
    activityFee) rides onto the order like the cancellation fee — a purely-scraped
    actual, written only when non-null."""
    order = _order(
        source="aggregator", aggregator_channel="Keeta 2.0", marketing_fee=None
    )

    await order_fees.stamp(
        _FakeDB(None),
        order,
        actual_commission=Decimal("9.00"),
        actual_marketing_fee=Decimal("3.00"),
    )
    assert order.marketing_fee == Decimal("3.00")
    assert order.aggregator_fee == Decimal("9.00")
