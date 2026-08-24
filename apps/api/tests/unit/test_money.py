"""
The rounding mode is the point of this module, so it is what gets tested.

Before `app/core/money.py` there were eight helpers under five names, and half
of them called `.quantize()` without a `rounding=` argument. Python's default
is `ROUND_HALF_EVEN` — bankers' rounding — so the same tie went two ways
depending on which file you happened to be in, over the same POS money.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core import money as m


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # The tie that used to disagree. Bankers' rounding gives 0.12 here,
        # because 2 is even, and 0.14 for 0.135, because 4 is even — so it is
        # not even consistently down.
        ("0.125", "0.13"),
        ("0.135", "0.14"),
        ("0.145", "0.15"),
        # Away from zero on the negative side too: a refund line rounds by the
        # same rule as the charge it reverses.
        ("-0.125", "-0.13"),
        # Ordinary cases, unchanged by any of this.
        ("1.234", "1.23"),
        ("1.235", "1.24"),
        ("10", "10.00"),
    ],
)
def test_money_rounds_half_away_from_zero(value, expected):
    assert m.money(value) == Decimal(expected)


def test_the_old_default_would_have_disagreed():
    """
    Pins the difference rather than describing it.

    If someone reintroduces a bare `.quantize(CENTS)` this is the line that
    says what they changed.
    """
    bankers = Decimal("0.125").quantize(m.CENTS)

    assert bankers == Decimal("0.12")
    assert m.money("0.125") == Decimal("0.13")


@pytest.mark.parametrize("blank", [None, ""])
def test_absent_figures_are_zero_not_an_error(blank):
    assert m.money(blank) == Decimal("0.00")
    assert m.to_decimal(blank) == Decimal(0)


def test_a_float_is_read_the_way_it_was_written():
    """
    `Decimal(1.15)` is 1.149999999999999911182158029987, and quantising that
    rounds down. Going through `str` keeps the value somebody typed.
    """
    assert m.money(1.15) == Decimal("1.15")


def test_to_decimal_does_not_round():
    """Terms are summed at full precision; only the total is quantised."""
    assert m.to_decimal("1.005") == Decimal("1.005")


def test_stock_and_cost_keep_their_own_precision():
    assert m.quantity("1.00005") == Decimal("1.0001")
    assert m.unit_cost("0.0000005") == Decimal("0.000001")


@pytest.mark.parametrize("junk", [None, "", "n/a", "abc", object()])
def test_money_or_none_answers_none_for_a_field_a_provider_omitted(junk):
    assert m.money_or_none(junk) is None


def test_money_or_none_rounds_like_everything_else():
    assert m.money_or_none("0.125") == Decimal("0.13")
