"""Shared order-filter building blocks — one definition of "by courier".

The admin dashboard and the orders list both filter and group orders by carrier,
and they must agree: a courier scorecard the operator clicks has to carry them to
the same rows on the list. So the mapping from a courier *code* to the SQL that
selects its orders lives here once, beside the mapping from an order back to its
code, rather than being spelt slightly differently in each place.

A "courier" here spans all three carrier shapes the shop uses:

* ``counter`` — a sale rung on a register (`source = cashier`), which has no
  carrier of its own;
* an **aggregator marketplace** (`talabat`, `keeta`, `noon_food`, `deliveroo`,
  `careem`), identified by the order's `aggregator_channel` display name; and
* a **dispatched website courier** (`lalamove`, `noon_send`, `slider`,
  `third_party`), identified by the order's delivery record.
"""

from __future__ import annotations

from sqlalchemy import and_, exists, or_

from app.models.order import Order
from app.models.order_delivery import OrderDelivery
from app.models.pos_order import OrderSourceEnum
from app.services.couriers import courier_catalog

#: The synthetic code for a counter sale — it is not a real carrier, but the
#: courier view treats "rung at the register" as one of the columns.
COUNTER_CODE = "counter"

#: Every courier code the dashboard and the list offer, counter first.
ALL_COURIER_CODES: list[str] = [COUNTER_CODE, *courier_catalog.COURIER_NAMES.keys()]

#: An aggregator code → the prefix its `aggregator_channel` display name starts
#: with, so a `keeta` filter catches "Keeta 2.0" too.
AGGREGATOR_CHANNEL_PREFIX: dict[str, str] = {
    "talabat": "talabat",
    "keeta": "keeta",
    "noon_food": "noon",
    "deliveroo": "deliveroo",
    "careem": "careem",
}


def courier_predicate(code: str):
    """A SQL predicate selecting the orders carried by `code`."""
    if code == COUNTER_CODE:
        return Order.source == OrderSourceEnum.CASHIER.value
    if code in courier_catalog.AGGREGATOR_CODES:
        prefix = AGGREGATOR_CHANNEL_PREFIX.get(code, code)
        return and_(
            Order.source == OrderSourceEnum.AGGREGATOR.value,
            Order.aggregator_channel.ilike(f"{prefix}%"),
        )
    # A dispatched website courier, matched on the order's delivery record.
    return exists().where(
        OrderDelivery.order_id == Order.id,
        OrderDelivery.provider == code,
    )


def courier_clause(codes: list[str] | None):
    """An OR over `courier_predicate` for a multi-select, or None for no filter."""
    preds = [courier_predicate(c) for c in (codes or []) if c]
    return or_(*preds) if preds else None


def courier_code_for(
    source: str | None,
    aggregator_channel: str | None,
    delivery_provider: str | None,
) -> str | None:
    """The courier code an order belongs to, or None when it has no carrier.

    The inverse of `courier_predicate`, for grouping a result set in Python (the
    dashboard's per-courier breakdown). A website order collected at the counter,
    or any order with no carrier and no register, returns None and is simply not
    counted under any courier.
    """
    if source == OrderSourceEnum.CASHIER.value:
        return COUNTER_CODE
    if source == OrderSourceEnum.AGGREGATOR.value:
        return courier_catalog.code_for_channel(aggregator_channel)
    if delivery_provider and delivery_provider in courier_catalog.COURIER_NAMES:
        return delivery_provider
    return None


def courier_label(code: str) -> str:
    """The display name for a courier code — "Counter" for the register."""
    if code == COUNTER_CODE:
        return "Counter"
    return courier_catalog.COURIER_NAMES.get(code, code.replace("_", " ").title())
