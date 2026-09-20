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
* a **dispatched website courier** (`lalamove`, `noon_send`, `slider_bike`,
  `slider_car`, `third_party`), identified by the order's delivery record.
"""

from __future__ import annotations

from sqlalchemy import and_, exists, or_

from app.core import search as search_text
from app.models.order import DeliveryMethodEnum, Order, OrderItem, OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.models.pos_order import OrderItemStatusEnum, OrderSourceEnum
from app.models.product import Product
from app.services.couriers import courier_catalog

#: The synthetic code for a counter sale — it is not a real carrier, but the
#: courier view treats "rung at the register" as one of the columns.
COUNTER_CODE = "counter"

#: The synthetic code for a store-pickup order (`source = online`,
#: `delivery_method = pickup`). Like the counter it has no carrier of its own,
#: but the shop tracks pickup as its own channel, so it is a column here rather
#: than folded into the website's dispatch couriers or counted under none.
WEBSITE_PICKUP_CODE = "website_pickup"

#: Every courier code the dashboard and the list offer, counter and store-pickup
#: first (the two synthetic, carrier-less columns).
ALL_COURIER_CODES: list[str] = [
    COUNTER_CODE,
    WEBSITE_PICKUP_CODE,
    *courier_catalog.COURIER_NAMES.keys(),
]

#: An aggregator code → the prefix its `aggregator_channel` display name starts
#: with, so a `keeta` filter catches "Keeta 2.0" too.
AGGREGATOR_CHANNEL_PREFIX: dict[str, str] = {
    "talabat": "talabat",
    "keeta": "keeta",
    "noon_food": "noon",
    "deliveroo": "deliveroo",
    "careem": "careem",
}


#: "The sale happened", for revenue and dashboard reads.
#:
#: A marketplace order is complete for US when the parcel leaves the counter — the
#: money is settled with the marketplace whatever the rider then does — and that is
#: the moment its check closes on the board. It used to be spelled `delivered`,
#: because an auto-close moved every aggregator order there five minutes after
#: packing. That claim was false (1,141 orders asserted a doorstep nobody had
#: reported) and has been dropped, so these reads have to name what they actually
#: mean instead of leaning on it: an aggregator order counts from
#: `out_for_delivery` onward, and only the channel's own status promotes it to
#: `delivered` later.
#:
#: A website order is NOT included at `out_for_delivery`: our own courier can still
#: fail one, and `undelivered` is a real outcome there. Nothing about that changed.
def fulfilled_clause():
    """SQLAlchemy predicate for an order whose sale stands."""
    from app.models.order import Order, OrderStatusEnum

    return or_(
        Order.status == OrderStatusEnum.DELIVERED.value,
        and_(
            Order.source == "aggregator",
            Order.status == OrderStatusEnum.OUT_FOR_DELIVERY.value,
        ),
    )


#: The statuses where an order is over and never became a sale — cancelled, its
#: payment failed, refunded, or disputed. Everything else (created … delivered,
#: and the non-terminal `undelivered`) is a live or completed order.
TERMINAL_STATUSES: tuple[str, ...] = (
    OrderStatusEnum.CANCELLED.value,
    OrderStatusEnum.PAYMENT_FAILED.value,
    OrderStatusEnum.REFUNDED.value,
    OrderStatusEnum.DISPUTED.value,
)


def active_or_fulfilled_clause():
    """SQLAlchemy predicate for an order that is a real, in-flight or completed sale.

    Broader than `fulfilled_clause` (delivered-only): it counts an order against
    its carrier from `confirmed` onward — `arrived_at_pos`, `packed`,
    `out_for_delivery`, `delivered` and the non-terminal `undelivered` — not only
    once the parcel is delivered. It excludes the terminal set (`cancelled`,
    `payment_failed`, `refunded`, `disputed`) **and** `created`: a `created` online
    order is a checkout that has not paid (an abandoned cart), and counting its
    total would inflate the courier scorecard's revenue with sales that never
    happened. Used by the dashboard's per-courier breakdown; the delivered KPI
    keeps `fulfilled_clause`.
    """
    from app.models.order import OrderStatusEnum as _S

    return Order.status.notin_((*TERMINAL_STATUSES, _S.CREATED.value))


def courier_predicate(code: str):
    """A SQL predicate selecting the orders carried by `code`."""
    if code == COUNTER_CODE:
        return Order.source == OrderSourceEnum.CASHIER.value
    if code == WEBSITE_PICKUP_CODE:
        return and_(
            Order.source == OrderSourceEnum.ONLINE.value,
            Order.delivery_method == DeliveryMethodEnum.PICKUP,
        )
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


def category_clause(category_ids: list | None):
    """Select orders holding at least one line in one of `category_ids`, or None.

    Unlike the courier/branch/legal-entity filters — each a column on the order —
    a category lives on the *line*: an order can span several categories, so this
    is an EXISTS over its `order_items`, joined to the product that carries the
    category. Voided counter lines (`status = 'void'`) do not make an order
    belong to a category; an off-counter line has a NULL status, so the match is
    `is_distinct_from('void')`, never `!= 'void'` (which NULL would fail).
    """
    ids = [c for c in (category_ids or []) if c]
    if not ids:
        return None
    return exists().where(
        OrderItem.order_id == Order.id,
        OrderItem.product_id == Product.id,
        Product.category_id.in_(ids),
        OrderItem.status.is_distinct_from(OrderItemStatusEnum.VOID.value),
    )


def item_search_clause(term: str | None):
    """Select orders holding a line whose product name or SKU matches `term`.

    Like `category_clause`, a product match lives on the *line*, not the order,
    so this is an EXISTS over `order_items`. It matches the line's frozen
    snapshot (`product_name`/`product_sku`) rather than joining `products`: the
    snapshot is what the customer actually ordered and survives a later rename or
    delete of the catalogue row, so a search keeps finding the historic order.
    Both columns go through the escaping-safe `contains` (case-insensitive
    substring). Voided counter lines (`status = 'void'`) do not make an order
    match; an off-counter line has a NULL status, so the guard is
    `is_distinct_from('void')`, never `!= 'void'` (which NULL would fail).
    """
    if not term:
        return None
    return exists().where(
        OrderItem.order_id == Order.id,
        or_(
            search_text.contains(OrderItem.product_name, term),
            search_text.contains(OrderItem.product_sku, term),
        ),
        OrderItem.status.is_distinct_from(OrderItemStatusEnum.VOID.value),
    )


def courier_code_for(
    source: str | None,
    aggregator_channel: str | None,
    delivery_provider: str | None,
    delivery_method: str | None = None,
) -> str | None:
    """The courier code an order belongs to, or None when it has no carrier.

    The inverse of `courier_predicate`, for grouping a result set in Python (the
    dashboard's per-courier breakdown). A store-pickup order (online + pickup)
    resolves to `website_pickup`; any order with no carrier, no register and no
    pickup returns None and is simply not counted under any courier.
    """
    if source == OrderSourceEnum.CASHIER.value:
        return COUNTER_CODE
    if source == OrderSourceEnum.AGGREGATOR.value:
        return courier_catalog.code_for_channel(aggregator_channel)
    if (
        source == OrderSourceEnum.ONLINE.value
        and delivery_method == DeliveryMethodEnum.PICKUP.value
    ):
        return WEBSITE_PICKUP_CODE
    if delivery_provider and delivery_provider in courier_catalog.COURIER_NAMES:
        return delivery_provider
    return None


def courier_label(code: str) -> str:
    """The display name for a courier code — "Counter" for the register."""
    if code == COUNTER_CODE:
        return "Counter"
    if code == WEBSITE_PICKUP_CODE:
        return "Store Pickup"
    return courier_catalog.COURIER_NAMES.get(code, code.replace("_", " ").title())
