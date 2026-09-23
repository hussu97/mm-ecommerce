"""The P&L subtotals, and the one shape both the API and the service agree on."""

from __future__ import annotations

from decimal import Decimal

from app.schemas.pnl import PnlStatement
from app.services.orders.order_pnl import CHANNELS, OrderPnl, statement_fields

D = Decimal


def _order(**over) -> OrderPnl:
    base = dict(
        gmv=D("105.00"),
        delivery_fees=D("21.00"),
        refunds=D("21.00"),
        output_vat=D("4.50"),
        cogs=D("5.71"),
        payment_fees=D("4.20"),
        commission=D("0"),
        marketplace_fees=D("0"),
        delivery_cost=D("10.50"),
        cancellation_charges=D("0"),
        fees_vat=D("0.70"),
        discounts=D("10.50"),
    )
    base.update(over)
    return OrderPnl(**base)


def test_the_subtotals_cascade():
    p = _order()
    assert p.net_revenue == D("79.50")
    assert p.pc1 == D("73.79")
    assert p.aggregator_and_delivery_fees == D("10.50")
    assert p.pc2 == D("80.79")
    assert p.pc3 == D("70.29")
    assert p.share(p.pc3) == D("66.94")
    assert p.net_vat == D("3.80")


def test_unknown_cogs_does_not_count_as_a_cost_and_stays_unknown():
    p = _order(cogs=None)
    assert p.pc1 == D("79.50")
    assert statement_fields(p)["cogs"] is None


def test_no_gmv_has_no_percentage():
    p = _order(
        gmv=D("0"), refunds=D("0"), discounts=D("0"), cancellation_charges=D("11.60")
    )
    assert p.share(p.pc3) is None


def test_the_service_fills_exactly_the_schema():
    assert set(statement_fields(_order())) == set(PnlStatement.model_fields)


def test_channel_codes_are_unique():
    assert len(CHANNELS) == len(set(CHANNELS))
