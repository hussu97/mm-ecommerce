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

from app.services.order_economics import OrderEconomics, processing_fee


def _economics(
    charged="105.00",
    items="85.00",
    courier="24.00",
    fee="4.05",
    refunded="0.00",
) -> OrderEconomics:
    return OrderEconomics(
        charged=Decimal(charged),
        items_value=Decimal(items),
        courier_cost=Decimal(courier) if courier is not None else None,
        processing_fee=Decimal(fee),
        processing_fee_is_estimated=True,
        refunded=Decimal(refunded),
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
