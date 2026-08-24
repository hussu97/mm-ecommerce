from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.pos.pos_pricing import (
    ChargeInput,
    DiscountInput,
    LineInput,
    apply_cash_rounding,
    calculate_order,
    money,
    split_inclusive_tax,
)

VAT = Decimal("0.05")
D = Decimal


def line(qty, price, **kw) -> LineInput:
    kw.setdefault("tax_rate", VAT)
    return LineInput(quantity=qty, unit_price=D(str(price)), **kw)


# ─── Inclusive tax split ──────────────────────────────────────────────────────


def test_inclusive_split_is_exact_for_a_clean_case():
    net, tax = split_inclusive_tax(D("105.00"), VAT)
    assert net == D("100.00")
    assert tax == D("5.00")


def test_inclusive_split_always_reconciles():
    """net + tax must equal the gross for every amount, despite rounding."""
    for cents in range(1, 400):
        gross = money(D(cents) / 100)
        net, tax = split_inclusive_tax(gross, VAT)
        assert net + tax == gross, gross


def test_zero_rate_leaves_the_amount_untouched():
    net, tax = split_inclusive_tax(D("42.00"), D("0"))
    assert (net, tax) == (D("42.00"), D("0.00"))


# ─── Line maths ───────────────────────────────────────────────────────────────


def test_single_line_totals():
    result = calculate_order([line(2, "35.00")])
    assert result.subtotal == D("70.00")
    assert result.total == D("70.00")
    assert result.tax_total == D("3.33")
    assert result.total_excl_tax == D("66.67")
    assert result.total_excl_tax + result.tax_total == result.total


def test_modifier_prices_are_added_per_unit():
    result = calculate_order([line(3, "20.00", options_price=D("5.00"))])
    assert result.subtotal == D("75.00")
    assert result.total == D("75.00")


def test_returned_units_stop_being_billed():
    result = calculate_order([line(5, "10.00", returned_quantity=2)])
    assert result.subtotal == D("30.00")
    assert result.total == D("30.00")


def test_fully_returned_line_contributes_nothing():
    result = calculate_order([line(2, "50.00", returned_quantity=2)])
    assert result.subtotal == D("0.00")
    assert result.total == D("0.00")
    assert result.tax_total == D("0.00")


# ─── Discounts ────────────────────────────────────────────────────────────────


def test_percentage_line_discount():
    result = calculate_order(
        [
            line(
                1,
                "100.00",
                discounts=[
                    DiscountInput("10% off", is_percentage=True, value=D("0.10"))
                ],
            )
        ]
    )
    assert result.line_discounts == D("10.00")
    assert result.total == D("90.00")


def test_fixed_line_discount_cannot_exceed_the_line():
    result = calculate_order(
        [line(1, "30.00", discounts=[DiscountInput("Comp", value=D("50.00"))])]
    )
    assert result.line_discounts == D("30.00")
    assert result.total == D("0.00")


def test_percentage_discount_respects_its_cap():
    discount = DiscountInput(
        "10% up to 5", is_percentage=True, value=D("0.10"), maximum_amount=D("5.00")
    )
    result = calculate_order([line(1, "200.00", discounts=[discount])])
    assert result.line_discounts == D("5.00")
    assert result.total == D("195.00")


def test_order_discount_applies_after_line_discounts():
    result = calculate_order(
        [line(1, "100.00", discounts=[DiscountInput("Ten", value=D("10.00"))])],
        order_discount=DiscountInput("Half", is_percentage=True, value=D("0.50")),
    )
    assert result.line_discounts == D("10.00")
    assert result.order_discount == D("45.00")  # 50% of the remaining 90
    assert result.total == D("45.00")


def test_order_discount_is_spread_so_mixed_rate_vat_stays_correct():
    """
    A zero-rated item and a 5% item on one check. An order-level discount must be
    apportioned, or the VAT line would be overstated.
    """
    taxed = line(1, "100.00")
    zero_rated = LineInput(quantity=1, unit_price=D("100.00"), tax_rate=D("0"))
    result = calculate_order(
        [taxed, zero_rated],
        order_discount=DiscountInput("10% off", is_percentage=True, value=D("0.10")),
    )

    assert result.order_discount == D("20.00")
    assert result.total == D("180.00")

    rates = {t.rate: t for t in result.taxes}
    # The 5% bucket sees 90.00 gross -> 85.71 net + 4.29 tax
    assert rates[VAT].amount == D("4.29")
    assert result.tax_total == D("4.29")
    assert result.total_excl_tax + result.tax_total == result.total


def test_discount_allocation_sums_exactly_despite_rounding():
    """Three equal lines and a discount that does not divide evenly by three."""
    lines = [line(1, "10.00") for _ in range(3)]
    result = calculate_order(
        [*lines], order_discount=DiscountInput("One off", value=D("0.01"))
    )
    assert result.order_discount == D("0.01")
    assert result.total == D("29.99")
    assert result.total_excl_tax + result.tax_total == result.total


# ─── Charges ──────────────────────────────────────────────────────────────────


def test_fixed_charge_is_added_with_its_tax():
    result = calculate_order(
        [line(1, "100.00")],
        charges=[ChargeInput("Delivery", "fixed", D("15.00"), tax_rate=VAT)],
    )
    assert result.charges == D("15.00")
    assert result.total == D("115.00")
    assert result.total_excl_tax + result.tax_total == result.total


def test_percentage_charge_is_taken_on_the_net_sale():
    result = calculate_order(
        [line(1, "105.00")],
        charges=[ChargeInput("Service", "percentage", D("0.10"), tax_rate=VAT)],
    )
    # Net sale is 100.00, so the 10% service charge is 10.00 (tax-exclusive base).
    assert result.charges == D("10.00")
    assert result.total_excl_tax + result.tax_total == result.total


def test_percentage_charges_do_not_compound():
    """Two percentage charges are each taken on the sale, not on each other."""
    result = calculate_order(
        [line(1, "105.00")],
        charges=[
            ChargeInput("Service", "percentage", D("0.10"), tax_rate=VAT),
            ChargeInput("Municipality", "percentage", D("0.10"), tax_rate=VAT),
        ],
    )
    # Net sale is 100.00, so each 10% charge is 10.00 — not 10.00 then 10.95.
    assert result.charge_amounts == [D("10.00"), D("10.00")]
    assert result.charges == D("20.00")
    assert result.total_excl_tax + result.tax_total == result.total


def test_non_revenue_line_is_untaxed():
    result = calculate_order([line(1, "50.00", is_non_revenue=True)])
    assert result.tax_total == D("0.00")
    assert result.total == D("50.00")


# ─── Cash rounding ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("amount", "step", "expected"),
    [
        # 10.12 is 0.12 from 10.00 and 0.13 from 10.25, so it rounds down.
        ("10.12", "0.25", "10.00"),
        ("10.11", "0.25", "10.00"),
        # 10.13 is 0.12 from 10.25, so it rounds up.
        ("10.13", "0.25", "10.25"),
        # Exact half rounds away from zero.
        ("10.125", "0.25", "10.25"),
        ("10.00", "0.25", "10.00"),
        ("10.37", "0.05", "10.35"),
        ("10.12", "0", "10.12"),
    ],
)
def test_cash_rounding(amount, step, expected):
    assert apply_cash_rounding(D(amount), D(step)) == D(expected)


def test_rounding_is_recorded_so_the_receipt_reconciles():
    result = calculate_order([line(1, "10.13")], cash_rounding_step=D("0.25"))
    assert result.total == D("10.25")
    assert result.rounding == D("0.12")
    assert result.total_excl_tax + result.tax_total + result.rounding == result.total


# ─── End to end ───────────────────────────────────────────────────────────────


def test_realistic_check_reconciles_end_to_end():
    """
    2 brownies @ 32, 1 latte @ 14 with a 3.00 extra shot, 10% off the latte line,
    5 AED off the whole order, 4.00 delivery charge, all at 5% VAT.
    """
    lines = [
        line(2, "32.00"),
        line(
            1,
            "14.00",
            options_price=D("3.00"),
            discounts=[DiscountInput("Staff 10%", is_percentage=True, value=D("0.10"))],
        ),
    ]
    result = calculate_order(
        lines,
        charges=[ChargeInput("Delivery", "fixed", D("4.00"), tax_rate=VAT)],
        order_discount=DiscountInput("5 off", value=D("5.00")),
    )

    assert result.subtotal == D("81.00")  # 64.00 + 17.00
    assert result.line_discounts == D("1.70")  # 10% of 17.00
    assert result.order_discount == D("5.00")
    assert result.charges == D("4.00")
    assert result.total == D("78.30")  # 81.00 - 1.70 - 5.00 + 4.00

    # The invoice must always reconcile.
    assert result.total_excl_tax + result.tax_total + result.rounding == result.total
    # And the per-rate tax breakdown must equal the tax total.
    assert sum(t.amount for t in result.taxes) == result.tax_total
