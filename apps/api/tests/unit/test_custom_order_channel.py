"""Custom orders (`source = custom`) as a sales channel in every report and filter.

A custom order never reaches the register, so it has no `is_pos` and no
`pos_status`, and it is sold when it is handed over. These pin the reading of
that: which rows the shared predicates keep, the code and label each report
gives the channel, and that the generic courier actions refuse one. The
database-backed halves live in `tests/integration/test_reports_agree.py` and
`tests/integration/test_order_pnl.py`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm.evaluator import _EvaluatorCompiler

from app.api.v1 import dashboard
from app.api.v1 import orders as orders_route
from app.core.exceptions import BadRequestError
from app.models.order import DeliveryMethodEnum, Order, OrderStatusEnum
from app.services.orders import order_pnl, order_query, tax_identity_service
from app.services.pos import daily_sales_email as dse
from app.services.pos.pos_reports import _base

S = OrderStatusEnum


def _evaluates(clause, order: Order) -> bool:
    """Apply a SQL predicate to an in-memory order, the way the ORM's
    `synchronize_session="evaluate"` does."""
    return bool(_EvaluatorCompiler(Order).process(clause)(order))


def _custom(status: OrderStatusEnum) -> Order:
    return Order(
        source="custom",
        status=status,
        is_pos=False,
        delivery_method=DeliveryMethodEnum.DELIVERY,
    )


def _sql(expr) -> str:
    return str(
        expr.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


# ── which custom orders are a sale ───────────────────────────────────────────


def test_a_delivered_custom_order_is_a_completed_sale():
    assert _evaluates(_base._COMPLETED_SALE, _custom(S.DELIVERED))


@pytest.mark.parametrize(
    "status", [S.CONFIRMED, S.ARRIVED_AT_POS, S.PACKED, S.OUT_FOR_DELIVERY]
)
def test_a_custom_order_is_not_a_sale_before_it_is_handed_over(status):
    # Out for delivery is not enough: unlike a marketplace order, the courier
    # carrying it is ours and can still fail it.
    assert not _evaluates(_base._COMPLETED_SALE, _custom(status))


def test_a_cancelled_custom_order_is_not_a_sale():
    assert not _evaluates(_base._COMPLETED_SALE, _custom(S.CANCELLED))


def test_the_reports_read_custom_orders_although_they_are_never_pos():
    assert _evaluates(_base._REPORTED_ORDER, _custom(S.DELIVERED))
    # Everything else still needs to have reached the register.
    assert not _evaluates(_base._REPORTED_ORDER, Order(source="online", is_pos=False))
    assert _evaluates(_base._REPORTED_ORDER, Order(source="online", is_pos=True))


# ── the channel's code and label in each report ──────────────────────────────


def test_pos_reports_name_the_custom_channel():
    assert _base._channel_labels([("custom",)]) == {"custom": "Custom orders"}
    # One of the shop's own channels: no marketplace badge.
    assert _base._channel_logo("custom") is None


def test_the_pnl_maps_a_custom_order_to_its_own_channel():
    assert "custom" in order_pnl.CHANNELS
    assert "WHEN (orders.source = 'custom') THEN 'custom'" in _sql(
        order_pnl.channel_expression()
    )


def test_the_dashboard_labels_the_custom_channel():
    assert dashboard._CHANNEL_LABELS["custom"] == "Custom orders"


def test_the_daily_email_gives_custom_orders_a_column():
    assert dse._column_for("custom", None) == "custom"
    # Whoever carried it: a courier does not move it into the website column.
    assert dse._column_for("custom", None, "delivery") == "custom"
    assert "custom" in dse._FIXED_COLUMNS
    assert dse._label("custom") == "custom orders"
    assert dse._order_channel_label("custom", None) == "custom order"


def test_order_query_groups_every_custom_order_under_custom():
    assert order_query.CUSTOM_CODE in order_query.ALL_COURIER_CODES
    assert order_query.courier_label("custom") == "Custom orders"
    # A custom order a courier carried is still the custom channel.
    assert order_query.courier_code_for("custom", None, None) == "custom"
    assert order_query.courier_code_for("custom", None, "slider_car") == "custom"
    assert order_query.courier_code_for("custom", None, "third_party") == "custom"


def test_the_custom_filter_selects_custom_orders_only():
    predicate = order_query.courier_predicate("custom")
    assert _evaluates(predicate, _custom(S.DELIVERED))
    assert not _evaluates(predicate, Order(source="online"))


def test_a_courier_filter_leaves_out_the_custom_orders_it_carried():
    """The scorecard counts those under `custom`; the list the click lands on
    must not show them under the courier as well."""
    sql = _sql(order_query.courier_predicate("slider_car"))
    assert "orders.source != 'custom'" in sql


def test_a_custom_order_is_fulfilled_once_delivered():
    assert _evaluates(order_query.fulfilled_clause(), _custom(S.DELIVERED))
    assert not _evaluates(order_query.fulfilled_clause(), _custom(S.OUT_FOR_DELIVERY))


def test_a_custom_order_is_taxed_as_a_website_order():
    assert tax_identity_service.channel_class_for("custom") == "website"


# ── the generic courier actions refuse a custom order ────────────────────────


def _admin():
    return SimpleNamespace(id=None, email="admin@example.com")


@pytest.mark.asyncio
async def test_dispatch_refuses_a_custom_order(monkeypatch):
    order = _custom(S.PACKED)
    result = MagicMock()
    result.scalars.return_value.first.return_value = order
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    dispatch = AsyncMock()
    monkeypatch.setattr(orders_route.courier_service, "dispatch", dispatch)

    with pytest.raises(BadRequestError, match="custom-order screen"):
        await orders_route.dispatch_order_delivery(
            "CO-20260926-001", SimpleNamespace(), db=db, admin=_admin()
        )
    # `courier_service.dispatch` falls back to a courier nobody chose.
    dispatch.assert_not_awaited()


@pytest.fixture
def custom_order(monkeypatch):
    order = _custom(S.UNDELIVERED)
    monkeypatch.setattr(orders_route, "_load_order", AsyncMock(return_value=order))
    load_delivery = AsyncMock()
    monkeypatch.setattr(orders_route, "_load_delivery", load_delivery)
    reassignment = MagicMock()
    for name in ("options_for", "quote", "move", "exposure_of", "abandon_booking"):
        setattr(reassignment, name, AsyncMock())
    monkeypatch.setattr(orders_route, "fulfilment_reassignment", reassignment)
    quote_for_order = AsyncMock()
    monkeypatch.setattr(
        orders_route.lalamove_service, "quote_for_order", quote_for_order
    )
    return SimpleNamespace(
        reassignment=reassignment,
        load_delivery=load_delivery,
        quote_for_order=quote_for_order,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        lambda: orders_route.order_fulfilment_options("CO-1", db=None, _admin=None),
        lambda: orders_route.quote_order_fulfilment(
            "CO-1",
            orders_route.FulfilmentQuoteRequest(provider="lalamove"),
            db=None,
            _admin=None,
        ),
        lambda: orders_route.reassign_order_fulfilment(
            "CO-1",
            orders_route.FulfilmentReassignRequest(provider="slider_car"),
            SimpleNamespace(),
            db=None,
            admin=None,
        ),
        lambda: orders_route.abandon_order_booking(
            "CO-1",
            orders_route.AbandonBookingRequest(acknowledged_charge=True),
            SimpleNamespace(),
            db=None,
            admin=None,
        ),
        lambda: orders_route.quote_lalamove_for_order("CO-1", db=None, _admin=None),
        lambda: orders_route.assign_order_to_lalamove(
            "CO-1",
            orders_route.LalamoveAssignRequest(quotation_id="q-1"),
            SimpleNamespace(),
            db=None,
            admin=None,
        ),
    ],
    ids=[
        "fulfilment-options",
        "fulfilment-quote",
        "reassign",
        "abandon-booking",
        "lalamove-quote",
        "lalamove-assign",
    ],
)
async def test_the_reassign_actions_refuse_a_custom_order(custom_order, call):
    with pytest.raises(BadRequestError, match="custom-order screen"):
        await call()
    for name in ("options_for", "quote", "move", "exposure_of", "abandon_booking"):
        getattr(custom_order.reassignment, name).assert_not_awaited()
    custom_order.quote_for_order.assert_not_awaited()
    custom_order.load_delivery.assert_not_awaited()
