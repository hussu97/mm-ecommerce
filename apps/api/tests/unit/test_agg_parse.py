"""The shared aggregator field parsers — the superset the five channels folded into."""

from __future__ import annotations

from decimal import Decimal

from app.services.providers._agg_parse import (
    DUBAI_TZ,
    first_present,
    parse_money,
)


def test_parse_money_reads_the_shapes_each_channel_emits():
    # already-parsed numbers
    assert parse_money(5) == Decimal("5")
    assert parse_money(3.5) == Decimal("3.5")
    assert parse_money(Decimal("2.40")) == Decimal("2.40")
    # human strings with the tokens the channels strip
    assert parse_money("1,234.50 AED") == Decimal("1234.50")
    assert parse_money("د.إ 10") == Decimal("10")
    assert parse_money("5.00\xa0AED") == Decimal("5.00")  # talabat's nbsp
    # a parenthesised negative (a credit on a statement line)
    assert parse_money("(5.00)") == Decimal("-5.00")
    # Careem's money object
    assert parse_money({"amount": 357.53, "currency": "AED"}) == Decimal("357.53")


def test_parse_money_returns_none_not_zero_for_absence():
    """A null fee and a zero fee are different claims — the whole point."""
    assert parse_money(None) is None
    assert parse_money("") is None
    assert parse_money("   ") is None
    assert parse_money("-") is None
    assert parse_money("n/a") is None
    assert parse_money(True) is None  # bool is not money, though it is an int
    assert parse_money({"currency": "AED"}) is None  # no amount
    # a real zero survives as zero, not None
    assert parse_money("0") == Decimal("0")
    assert parse_money(0) == Decimal("0")


def test_first_present_picks_the_first_non_null_key():
    row = {"a": None, "b": 0, "c": "x"}
    assert first_present(row, "a", "b", "c") == 0  # 0 is present, not skipped
    assert first_present(row, "a", "c") == "x"  # None 'a' skipped
    assert first_present(row, "missing") is None
    assert first_present("not a dict", "a") is None


def test_dubai_tz_is_the_business_timezone():
    assert str(DUBAI_TZ) == "Asia/Dubai"
