"""
What the shop keeps, and the two percentages that answer different questions.

The order screen showed what the customer paid and what the courier cost, and
stopped — so the number that decides whether the order was worth taking was the
one nobody could see. MM-20260815-001 is the worked example: AED 20 charged for
delivery against a 24-dirham van, which the screen showed as a red margin on the
*delivery* while saying nothing about the order.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.services.orders.order_economics import OrderEconomics, processing_fee


def _economics(
    charged="105.00",
    items="85.00",
    courier="24.00",
    fee="4.05",
    refunded="0.00",
    before_discount="85.00",
    aggregator=None,
    cancellation=None,
    expects_cost_of_sale=True,
) -> OrderEconomics:
    return OrderEconomics(
        charged=Decimal(charged),
        items_value=Decimal(items),
        items_before_discount=Decimal(before_discount),
        courier_cost=Decimal(courier) if courier is not None else None,
        aggregator_fee=Decimal(aggregator) if aggregator is not None else None,
        processing_fee=Decimal(fee),
        processing_fee_is_estimated=True,
        refunded=Decimal(refunded),
        cancellation_fee=Decimal(cancellation) if cancellation is not None else None,
        expects_cost_of_sale=expects_cost_of_sale,
    )


# ── the fee ───────────────────────────────────────────────────────────────────


def test_the_fee_is_the_gateway_rate_plus_the_fixed_part_plus_vat():
    """2.9% of 100 + 1 = 3.90 before tax, and the shop pays 5% on top of it."""
    gateway = SimpleNamespace(fee_percent=Decimal("2.9"), fee_fixed=Decimal("1"))
    fee, estimated = processing_fee(Decimal("100.00"), gateway)
    assert fee == Decimal("4.10")
    assert estimated is True


def test_the_fee_carries_vat_because_the_processor_invoices_the_shop():
    """
    The screen used to report the fee before tax, which overstated what every
    card order nets by 5% of it. The order this was noticed on: 54.00 charged,
    24.00 to the courier, and a processing line reading 2.57 when the invoice
    says 2.69 — so the shop's net was 27.43 on screen and 27.31 in the bank.

    Distinct from the VAT on the order itself, which is *inclusive* — already
    inside what the customer paid. A processor quotes before tax and adds it.
    """
    fee, _ = processing_fee(Decimal("54.00"), None)
    assert fee == Decimal("2.69")
    before_tax = Decimal("54.00") * Decimal("0.029") + Decimal("1")
    assert fee > before_tax


def test_a_gateway_we_cannot_find_still_charges_the_going_rate():
    """
    A missing row is a seeding problem, not a free transaction. Showing zero
    would flatter every order on the screen.
    """
    fee, estimated = processing_fee(Decimal("100.00"), None)
    assert fee == Decimal("4.10")
    assert estimated is True


def test_a_renegotiated_rate_is_an_edit_and_not_a_deploy():
    gateway = SimpleNamespace(fee_percent=Decimal("2.4"), fee_fixed=Decimal("0.50"))
    fee, _ = processing_fee(Decimal("100.00"), gateway)
    # 2.4% of 100 + 0.50 = 2.90, and 5% on top.
    assert fee == Decimal("3.05")


def test_nothing_charged_costs_nothing_to_process():
    fee, estimated = processing_fee(Decimal("0"), None)
    assert fee == Decimal("0")
    assert estimated is False


# ── what is left ──────────────────────────────────────────────────────────────


def test_net_is_what_survives_the_van_and_the_processor():
    # 105 in, 24 to the courier, 4.05 to Stripe.
    assert _economics().net == Decimal("76.95")


def test_a_refund_comes_off_the_top():
    """
    The processor generally does not hand its fee back with the money, so a
    refunded order nets *less* than nothing was ever taken.
    """
    assert _economics(refunded="85.00").net == Decimal("-8.05")


def test_a_third_party_order_has_no_courier_cost_rather_than_a_free_one():
    """
    Nobody invoices us per order on a third-party zone. That is a real "we do
    not know", and treating it as zero would quietly claim the van was free.
    """
    result = _economics(courier=None)
    assert result.courier_cost is None
    assert result.net == Decimal("100.95")


# ── the two percentages ───────────────────────────────────────────────────────


def test_the_two_margins_answer_different_questions():
    result = _economics()
    # Of everything the customer handed over: 76.95 / 105.
    assert result.margin_on_charged == Decimal("73.29")
    # Of the cake alone: 76.95 / 85. Higher, because the delivery fee is in the
    # numerator and not the denominator.
    assert result.margin_on_items == Decimal("90.53")


def test_free_delivery_is_where_the_second_number_earns_its_place():
    """
    45 of cake, no delivery charged, a 13-dirham van. Against the total it looks
    like a thin order; against the items it says exactly what the cake is
    carrying — which is the number that matters when pricing the cake.
    """
    result = _economics(charged="45.00", items="45.00", courier="13.00", fee="2.31")
    assert result.net == Decimal("29.69")
    assert result.margin_on_charged == result.margin_on_items == Decimal("65.98")


def test_a_zero_total_order_has_no_percentage_rather_than_a_zero_one():
    """
    A full-discount staff order, or a replacement sent out free. There is no
    share of nothing, and 0 would read as "we kept none of it".
    """
    result = _economics(charged="0.00", items="0.00", courier="13.00", fee="0.00")
    assert result.margin_on_charged is None
    assert result.margin_on_items is None
    assert result.net == Decimal("-13.00"), "the van still cost us"


# ── is the order paying for itself? ───────────────────────────────────────────


def test_the_commission_comes_off_the_net_like_a_van_would():
    """
    An aggregator order has no courier cost and is not therefore free to fulfil.

    Its cost of sale is the marketplace's cut, and until that was modelled every
    Talabat order on the screen showed a net that was a quarter too high.
    """
    e = _economics(charged="100.00", courier=None, fee="0.00", aggregator="26.25")
    assert e.net == Decimal("73.75")


def test_a_cancellation_fee_comes_off_the_net_too():
    """
    The marketplace's cancellation / customer-compensation charge is a cost the
    shop bears — neither commission nor processing — so it nets the order down
    like the commission does, on its own line.
    """
    e = _economics(
        charged="100.00",
        courier=None,
        fee="0.00",
        aggregator="26.25",
        cancellation="4.00",
    )
    assert e.net == Decimal("69.75")


def test_no_cancellation_fee_leaves_the_net_untouched():
    """A null cancellation fee (the usual case) subtracts nothing."""
    e = _economics(charged="100.00", courier=None, fee="0.00", aggregator="26.25")
    assert e.cancellation_fee is None
    assert e.net == Decimal("73.75")


def test_cost_cover_is_measured_against_menu_price_not_what_was_charged():
    """
    The denominator is held still on purpose.

    Half-price basket, and the order still nets 45 of a 100-dirham menu value.
    Measured against the discounted 50 it would read as 90% and look excellent;
    measured against the menu price it reads as 45% and fails — which is the
    truth, because the discount is a cost the shop chose to bear.
    """
    e = _economics(
        charged="50.00",
        items="50.00",
        before_discount="100.00",
        courier="5.00",
        fee="0.00",
    )
    assert e.cost_cover == Decimal("45.00")
    assert e.covers_direct_cost is False


def test_an_order_that_clears_the_bar_says_so():
    e = _economics(
        charged="100.00",
        items="100.00",
        before_discount="100.00",
        courier="10.00",
        fee="5.00",
    )
    assert e.cost_cover == Decimal("85.00")
    assert e.covers_direct_cost is True


def test_exactly_at_the_bar_counts_as_covering_it():
    """`>=`, not `>`. Fifty percent is the bar, not the thing to beat."""
    e = _economics(
        charged="100.00",
        items="100.00",
        before_discount="100.00",
        courier="50.00",
        fee="0.00",
    )
    assert e.cost_cover == Decimal("50.00")
    assert e.covers_direct_cost is True


def test_an_unknown_cost_of_sale_answers_neither_yes_nor_no():
    """
    The three-valued answer, and the reason for it.

    A Talabat order whose commission rate nobody has configured has an
    unknowable net. Answering `False` would file it beside the orders that are
    genuinely losing money, and a seeding gap would become a fortnight of
    investigating the wrong orders.
    """
    e = _economics(
        charged="100.00",
        items="100.00",
        before_discount="100.00",
        courier=None,
        aggregator=None,
        fee="0.00",
        expects_cost_of_sale=True,
    )
    assert e.covers_direct_cost is None


def test_a_counter_sale_has_no_cost_of_sale_and_is_still_answerable():
    """
    Nobody carried it and no marketplace sold it, so "neither fee is set" is the
    complete truth rather than a gap — and the question can be answered.
    """
    e = _economics(
        charged="100.00",
        items="100.00",
        before_discount="100.00",
        courier=None,
        aggregator=None,
        fee="0.00",
        expects_cost_of_sale=False,
    )
    assert e.covers_direct_cost is True


def test_an_order_with_no_goods_has_no_share_to_report():
    """A zero-total replacement. There is no percentage of nothing."""
    e = _economics(
        charged="0.00",
        items="0.00",
        before_discount="0.00",
        courier=None,
        fee="0.00",
        expects_cost_of_sale=False,
    )
    assert e.cost_cover is None
    assert e.covers_direct_cost is None
