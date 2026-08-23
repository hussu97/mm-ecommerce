"""
A fully refunded order must be voidable.

The guard used to test whether any payment rows existed. Because a refund is
itself a payment row, following the error's own advice — "refund them before
voiding" — left the cashier exactly where they started, with an order that
could never be voided and money already handed back.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

from app.services.pos import pos_order_service


class _Payment:
    def __init__(self, amount, is_refund=False):
        self.amount = Decimal(amount)
        self.is_refund = is_refund


class _Order:
    def __init__(self, *payments):
        self.payments = list(payments)


def test_an_unpaid_order_holds_nothing():
    assert pos_order_service._net_paid(_Order()) == Decimal("0")


def test_a_paid_order_holds_the_money():
    order = _Order(_Payment("262.00"))
    assert pos_order_service._net_paid(order) == Decimal("262.00")


def test_a_fully_refunded_order_holds_nothing():
    """The case that was stuck: paid, then refunded in full."""
    order = _Order(_Payment("262.00"), _Payment("262.00", is_refund=True))
    assert pos_order_service._net_paid(order) == Decimal("0")


def test_a_partial_refund_still_holds_the_remainder():
    """A part-refunded order must stay unvoidable — money is still held."""
    order = _Order(_Payment("262.00"), _Payment("62.00", is_refund=True))
    assert pos_order_service._net_paid(order) == Decimal("200.00")


def test_split_tender_sums():
    order = _Order(_Payment("100.00"), _Payment("162.00"))
    assert pos_order_service._net_paid(order) == Decimal("262.00")


def test_the_void_guard_tests_the_net_not_the_row_count():
    """Guards the arithmetic above against the real code drifting from it."""
    source = inspect.getsource(pos_order_service.void_order)
    assert "_net_paid(order) != 0" in source
    assert "if order.payments:" not in source
