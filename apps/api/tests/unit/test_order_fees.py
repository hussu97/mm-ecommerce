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

from app.models.courier_branch_rate import CourierBranchRate
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


def _override(
    commission_percent=None,
    commission_fixed=None,
    payment_fee_percent=None,
    payment_fee_fixed=None,
) -> SimpleNamespace:
    """A `courier_branch_rate` row — every column null unless the test sets it."""
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
    """A session that routes each `select(...)` to the row it was handed.

    The commission path now reads two tables — the courier and its optional
    per-branch override — so the fake answers by which entity was queried, and
    still returns `row` for the single-select gateway path the own-channel tests
    use.
    """

    def __init__(self, row=None, *, override=None):
        self._row = row
        self._override = override

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        result = self._override if entity is CourierBranchRate else self._row
        return SimpleNamespace(scalar_one_or_none=lambda r=result: r)


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


# ── the four contracts, each read its own way ────────────────────────────────
#
# Deliveroo per-branch, Keeta's net-of-base VAT-inclusive figure, Careem's cash
# waiver, Talabat's member fee (dormant) — the grammar `137` could not express.


@pytest.mark.asyncio
async def test_deliveroo_takes_its_branch_rate_not_a_courier_default():
    """
    27% in Sharjah, 31% in Barsha, on the same courier row.

    The branch override wins over the (here absent) courier default, so a Barsha
    basket of 100 is 31 + VAT = 32.55. Payment is a real zero — Deliveroo takes
    no card fee — not the null an unpriced channel would show.
    """
    courier = _courier(payment_fee_percent="0.00")  # commission is per-branch only
    order = _order(
        source="aggregator",
        aggregator_channel="Deliveroo",
        branch_id=uuid.uuid4(),
    )

    barsha = await order_fees.compute(
        _FakeDB(courier, override=_override(commission_percent="31.00")), order
    )
    assert barsha.aggregator_fee == Decimal("32.55")  # 31 + 5%
    assert barsha.payment_fee == Decimal("0.00")

    sharjah = await order_fees.compute(
        _FakeDB(courier, override=_override(commission_percent="27.00")), order
    )
    assert sharjah.aggregator_fee == Decimal("28.35")  # 27 + 5%


@pytest.mark.asyncio
async def test_deliveroo_at_a_branch_with_no_override_is_unknown_not_free():
    """No branch rate means we cannot cost the basket — null, never zero."""
    courier = _courier(payment_fee_percent="0.00")
    order = _order(
        source="aggregator", aggregator_channel="Deliveroo", branch_id=uuid.uuid4()
    )

    fees = await order_fees.compute(_FakeDB(courier, override=None), order)

    assert fees.aggregator_fee is None
    assert fees.payment_fee == Decimal("0.00")


@pytest.mark.asyncio
async def test_keeta_nets_the_flat_out_of_the_base_and_carries_its_own_vat():
    """
    "4 AED + 25% of (the basket − 4 AED)", VAT already inside both fees.

    On a 40 basket: 4 + 25%·(40 − 4) = 4 + 9 = 13, and no 5% on top because the
    contract is quoted VAT-inclusive. Payment is 2% of 40 = 0.80, also inclusive.
    Read either figure as before-VAT and it is 5% light — the mistake the flags
    exist to stop.
    """
    courier = _courier(
        commission_percent="25.00",
        commission_fixed="4.00",
        commission_fixed_net_of_base=True,
        commission_vat_inclusive=True,
        payment_fee_percent="2.00",
        payment_fee_vat_inclusive=True,
    )
    order = _order(
        source="aggregator", aggregator_channel="Keeta 2.0", total=Decimal("40.00")
    )

    fees = await order_fees.compute(_FakeDB(courier), order)

    assert fees.aggregator_fee == Decimal("13.00")
    assert fees.payment_fee == Decimal("0.80")


@pytest.mark.asyncio
async def test_careem_waives_its_payment_fee_on_a_cash_order():
    """
    2% only when the customer paid the marketplace by card.

    A `postpaid` (cash) order took no card, so it pays no card fee — a true zero,
    the same rule a cash counter sale gets. A `prepaid` one pays 2% + VAT.
    Commission (25% + VAT) is charged either way.
    """
    courier = _courier(
        commission_percent="25.00",
        commission_fixed="4.00",
        commission_fixed_requires_member=True,
        payment_fee_percent="2.00",
        payment_fee_cash_exempt=True,
    )

    cash = await order_fees.compute(
        _FakeDB(courier),
        _order(
            source="aggregator",
            aggregator_channel="Careem",
            aggregator_payment_type="postpaid",
        ),
    )
    assert cash.payment_fee == Decimal("0.00")
    assert cash.aggregator_fee == Decimal("26.25")  # 25% of 100 + VAT, no member fee

    card = await order_fees.compute(
        _FakeDB(courier),
        _order(
            source="aggregator",
            aggregator_channel="Careem",
            aggregator_payment_type="prepaid",
        ),
    )
    assert card.payment_fee == Decimal("2.10")  # 2 + VAT


@pytest.mark.asyncio
async def test_talabat_charges_its_payment_fee_even_on_a_cash_order():
    """Talabat bills the 2% on every order — no cash waiver, unlike Careem."""
    courier = _courier(
        commission_percent="30.00",
        commission_fixed="4.00",
        commission_fixed_requires_member=True,
        payment_fee_percent="2.00",
        # payment_fee_cash_exempt stays False
    )
    order = _order(
        source="aggregator",
        aggregator_channel="Talabat",
        aggregator_payment_type="postpaid",
    )

    fees = await order_fees.compute(_FakeDB(courier), order)

    assert fees.aggregator_fee == Decimal("31.50")  # 30% of 100 + VAT (no member fee)
    assert fees.payment_fee == Decimal("2.10")  # 2 + VAT, cash notwithstanding


@pytest.mark.asyncio
async def test_the_member_flat_fee_is_dormant_until_the_order_says_member():
    """
    The 4 AED is charged only to a member, and we cannot yet see who is one.

    An order with membership unknown (the only state today) is not charged the
    flat — the commission is the percentage alone. Flip the order's flag true and
    the 4 AED lands, so the rule is wired and merely waiting on a signal.
    """
    courier = _courier(
        commission_percent="30.00",
        commission_fixed="4.00",
        commission_fixed_requires_member=True,
    )

    unknown = await order_fees.compute(
        _FakeDB(courier),
        _order(source="aggregator", aggregator_channel="Talabat"),
    )
    assert unknown.aggregator_fee == Decimal("31.50")  # 30 + VAT, no flat

    member = await order_fees.compute(
        _FakeDB(courier),
        _order(
            source="aggregator",
            aggregator_channel="Talabat",
            aggregator_customer_is_member=True,
        ),
    )
    assert member.aggregator_fee == Decimal("35.70")  # (30 + 4) + VAT
