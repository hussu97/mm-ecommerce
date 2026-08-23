from __future__ import annotations

from decimal import Decimal

from app.services.inventory.transfer_service import (
    production_output,
    production_unit_cost,
)

D = Decimal


# ─── Yield ────────────────────────────────────────────────────────────────────


def test_full_yield_produces_what_was_asked_for():
    assert production_output(D("100"), D("1")) == D("100.0000")


def test_yield_loss_reduces_the_output():
    """A 10% loss on a 100-unit batch leaves 90."""
    assert production_output(D("100"), D("0.9")) == D("90.0000")


def test_heavy_yield_loss():
    assert production_output(D("50"), D("0.75")) == D("37.5000")


def test_missing_yield_is_treated_as_no_loss():
    assert production_output(D("40"), None) == D("40.0000")


def test_nonsensical_yield_falls_back_rather_than_destroying_the_batch():
    """A zero or negative yield would otherwise produce nothing from real inputs."""
    assert production_output(D("40"), D("0")) == D("40.0000")
    assert production_output(D("40"), D("-1")) == D("40.0000")


# ─── Unit cost ────────────────────────────────────────────────────────────────


def test_unit_cost_spreads_inputs_over_output():
    assert production_unit_cost(D("450"), D("90")) == D("5.000000")


def test_yield_loss_raises_the_unit_cost():
    """
    The point of modelling yield: 450 of ingredients that yields 90 units costs
    5.00 each, whereas a lossless 100 units would cost 4.50. The waste has to
    land somewhere, and it lands in cost per unit.
    """
    lossless = production_unit_cost(D("450"), production_output(D("100"), D("1")))
    lossy = production_unit_cost(D("450"), production_output(D("100"), D("0.9")))
    assert lossless == D("4.500000")
    assert lossy == D("5.000000")
    assert lossy > lossless


def test_zero_output_does_not_divide_by_zero():
    assert production_unit_cost(D("450"), D("0")) == D("0.000000")


def test_zero_input_cost_gives_zero_unit_cost():
    assert production_unit_cost(D("0"), D("90")) == D("0.000000")


def test_total_value_is_conserved_across_production():
    """
    Whatever went in must come out: net_output x unit_cost equals the input cost,
    to the rounding precision of the cost field.
    """
    input_cost = D("1234.56")
    net = production_output(D("200"), D("0.85"))
    unit = production_unit_cost(input_cost, net)
    assert (net * unit).quantize(D("0.01")) == input_cost.quantize(D("0.01"))
