"""The one registry of fees a customer pays on top of the goods, and the
screens that must count them: the fulfilment margin, the reassignment quote,
the receipt's fee lines."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql

from app.api.v1.orders import OrderDeliveryResponse
from app.models.order import DeliveryMethodEnum, Order
from app.models.order_delivery import OrderDelivery
from app.schemas.surcharge import SurchargeLine
from app.services.orders import order_surcharges

D = Decimal


def _order(**over) -> SimpleNamespace:
    base = dict(
        delivery_method=DeliveryMethodEnum.DELIVERY,
        delivery_fee=D("15.00"),
        low_order_fee=D("15.00"),
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_the_small_order_fee_is_a_surcharge():
    [s] = order_surcharges.surcharges(_order())
    assert (s.code, s.label, s.amount) == (
        "low_order_fee",
        "Small order fee",
        D("15.00"),
    )
    assert order_surcharges.surcharges_total(_order()) == D("15.00")


def test_a_zero_fee_is_not_listed():
    assert order_surcharges.surcharges(_order(low_order_fee=D("0"))) == []
    assert order_surcharges.surcharges_total(_order(low_order_fee=None)) == D("0")


def test_a_non_number_costs_a_line_not_a_crash():
    # An unloaded column, or a mock in a caller's test, must not 500 a receipt.
    assert order_surcharges.surcharges(MagicMock()) == []


def test_receipt_fee_lines_put_delivery_first():
    lines = order_surcharges.fee_lines(_order())
    assert [(x.code, x.label) for x in lines] == [
        ("delivery_fee", "Delivery fee"),
        ("low_order_fee", "Small order fee"),
    ]


def test_a_pickup_fee_is_labelled_as_one():
    lines = order_surcharges.fee_lines(
        _order(delivery_method=DeliveryMethodEnum.PICKUP, low_order_fee=D("0"))
    )
    assert [x.label for x in lines] == ["Pickup fee"]


def test_free_delivery_prints_no_delivery_line():
    lines = order_surcharges.fee_lines(_order(delivery_fee=D("0")))
    assert [x.code for x in lines] == ["low_order_fee"]


def test_every_registered_column_exists_on_orders():
    for column in order_surcharges.COLUMNS:
        assert column in Order.__table__.columns


def test_the_sql_sum_names_every_column_and_starts_from_a_literal_zero():
    sql = str(order_surcharges.sql_total(Order).compile(dialect=postgresql.dialect()))
    for column in order_surcharges.COLUMNS:
        assert f"orders.{column}" in sql
    # asyncpg cannot type an untyped bind added to a numeric column.
    assert sql.startswith("0 + ")


def _delivery(**over) -> OrderDelivery:
    base = dict(
        provider="slider_car",
        fee_charged=D("15.00"),
        quoted_cost=D("44.80"),
        driver_assignment_count=0,
    )
    base.update(over)
    return OrderDelivery(**base)


def test_the_fulfilment_margin_counts_the_small_order_fee():
    r = OrderDeliveryResponse.of(_delivery(), order=_order())
    assert r.margin == -14.80  # 15 delivery + 15 small order − 44.80
    assert [(s.code, s.amount) for s in r.surcharges] == [("low_order_fee", 15.0)]


def test_without_the_order_the_margin_is_delivery_alone():
    r = OrderDeliveryResponse.of(_delivery())
    assert r.margin == -29.80
    assert r.surcharges == []


def test_the_schema_line_mirrors_the_registry():
    assert SurchargeLine.fees_of(_order()) == [
        SurchargeLine(code="delivery_fee", label="Delivery fee", amount=15.0),
        SurchargeLine(code="low_order_fee", label="Small order fee", amount=15.0),
    ]
    assert SurchargeLine.surcharges_of(None) == []
