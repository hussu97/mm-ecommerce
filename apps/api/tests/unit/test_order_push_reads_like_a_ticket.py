"""
What a register reads when an order lands.

The push is the counter's first look at an order, so it has to answer the
questions a person asks in that order: which order and for how much (the title),
then which channel, when it is due, and who it is for (the body, most-useful
first). Anything we do not have is left out rather than filled with a stand-in —
a blank line reads as "nothing on file", an invented "A customer" reads as a
real person who is not there.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.services import push_service


def _order(**overrides):
    base = dict(
        order_number="MM-1024",
        total=Decimal("85.00"),
        source="online",
        aggregator_channel=None,
        aggregator_driver_name=None,
        promised_at=None,
        promised_precision=None,
        delivery_method="delivery",
        customer_name=None,
        customer_phone=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_website_order_channel_line_says_website():
    assert push_service._channel_line(_order(source="online")) == "Website"


def test_aggregator_order_names_the_marketplace_not_the_word_aggregator():
    order = _order(source="aggregator", aggregator_channel="Talabat")
    assert push_service._channel_line(order) == "Talabat"


def test_aggregator_channel_carries_the_rider_once_assigned():
    order = _order(
        source="aggregator",
        aggregator_channel="Noon",
        aggregator_driver_name="Ahmed",
    )
    assert push_service._channel_line(order) == "Noon · Ahmed"


def test_a_missing_channel_falls_back_rather_than_showing_nothing():
    order = _order(source="aggregator", aggregator_channel=None)
    assert push_service._channel_line(order) == "Aggregator"


def test_no_customer_line_when_we_know_neither_name_nor_phone():
    assert push_service._customer_line(_order()) is None


def test_customer_line_is_name_and_phone_when_we_have_both():
    order = _order(customer_name="Sara", customer_phone="+971500000000")
    assert push_service._customer_line(order) == "Sara · +971500000000"


def test_customer_line_is_just_the_phone_when_that_is_all_we_have():
    order = _order(customer_name=None, customer_phone="+971500000000")
    assert push_service._customer_line(order) == "+971500000000"


def test_no_when_line_without_a_promised_time():
    assert push_service._when_line(_order(promised_at=None)) is None


def test_delivery_line_carries_date_and_time():
    soon = datetime.now(timezone.utc) + timedelta(days=2)
    order = _order(promised_at=soon, promised_precision="time", delivery_method="delivery")
    line = push_service._when_line(order)
    assert line.startswith("Delivery: ")
    # A time-precise promise shows a clock; AM/PM is the tell.
    assert ("AM" in line) or ("PM" in line)


def test_pickup_line_is_labelled_pickup():
    soon = datetime.now(timezone.utc) + timedelta(days=2)
    order = _order(promised_at=soon, promised_precision="time", delivery_method="pickup")
    assert push_service._when_line(order).startswith("Pickup: ")


def test_a_day_precision_promise_carries_no_clock():
    soon = datetime.now(timezone.utc) + timedelta(days=2)
    order = _order(promised_at=soon, promised_precision="day")
    line = push_service._when_line(order)
    assert "AM" not in line and "PM" not in line
