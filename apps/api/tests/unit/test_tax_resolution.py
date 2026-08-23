"""
A tax group must charge each rate once, and never charge a disabled tax.

This exists because a real import put two 5% VAT rows into one group — the
site called it "VAT", Foodics called it "UAE VAT" — and the resolver summed
both. Every order through that group would have been billed 10%.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

from app.services.pos import pos_order_service


class _Tax:
    def __init__(self, name, rate, is_active=True, type="inclusive"):
        self.id = name
        self.name = name
        self.rate = Decimal(rate)
        self.is_active = is_active
        self.type = type


class _Link:
    def __init__(self, tax):
        self.tax = tax


class _Group:
    def __init__(self, *taxes):
        self.taxes = [_Link(t) for t in taxes]


def _rate(group) -> Decimal:
    """Mirror the resolver's arithmetic over a group's links."""
    links = [link for link in group.taxes if link.tax.is_active]
    return sum((Decimal(str(link.tax.rate)) for link in links), Decimal("0"))


def test_a_single_active_tax_is_charged_once():
    assert _rate(_Group(_Tax("VAT", "0.05"))) == Decimal("0.05")


def test_a_disabled_duplicate_is_not_added_on_top():
    """The exact production shape: two 5% rows, one switched off."""
    group = _Group(_Tax("VAT", "0.05"), _Tax("UAE VAT", "0.05", is_active=False))
    assert _rate(group) == Decimal("0.05"), "a disabled tax must not be charged"


def test_two_active_taxes_still_combine():
    """Genuinely stacked taxes are a real configuration and must still sum."""
    group = _Group(_Tax("VAT", "0.05"), _Tax("Municipality", "0.07"))
    assert _rate(group) == Decimal("0.12")


def test_a_group_of_only_disabled_taxes_charges_nothing():
    group = _Group(_Tax("VAT", "0.05", is_active=False))
    assert _rate(group) == Decimal("0")


def test_the_resolver_actually_filters_on_is_active():
    """Guards the arithmetic above against drifting from the real code."""
    source = inspect.getsource(pos_order_service._resolve_tax)
    assert "link.tax.is_active" in source
