"""The better / same / worse verdict the new-PO form shows beside each line."""

from __future__ import annotations

from decimal import Decimal as D

from app.services.inventory.po_cost_check_service import SAME_WITHIN, compare


def test_paying_more_is_worse_and_reports_the_fraction():
    c = compare(D("10.50"), D("10.00"))
    assert c.verdict == "worse"
    assert c.difference == D("0.5")
    assert c.change == D("0.0500")
    assert c.reference_cost == D("10.00")


def test_paying_less_is_better_with_a_negative_difference():
    c = compare(D("9.00"), D("10.00"))
    assert c.verdict == "better"
    assert c.difference == D("-1")
    assert c.change == D("-0.1000")


def test_within_the_band_is_the_same_either_side():
    # 0.5% of 10.00 is 0.05: the edge itself still reads as the same price.
    assert SAME_WITHIN == D("0.005")
    assert compare(D("10.05"), D("10.00")).verdict == "same"
    assert compare(D("9.95"), D("10.00")).verdict == "same"
    assert compare(D("10.06"), D("10.00")).verdict == "worse"
    assert compare(D("9.94"), D("10.00")).verdict == "better"


def test_a_free_reference_has_no_percentage():
    worse = compare(D("1.00"), D("0"))
    assert worse.verdict == "worse"
    assert worse.change is None
    same = compare(D("0"), D("0"))
    assert same.verdict == "same"
    assert same.change is None
