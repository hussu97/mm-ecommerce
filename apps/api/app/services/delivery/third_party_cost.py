"""What a third-party courier charged, entered by hand after delivery.

A third party is a courier we use outside any integration: nothing is booked
through us and no invoice reaches us per order, so the system never learns its
fare. Its checkout quote is only a backup estimate (MM-20261003-002 booked a
49.00 quote as cost on a delivery nobody had priced). So a third-party
delivery's cost is optional and manual: once the order is delivered, a person
may record what was paid, and that is what `OrderDelivery.courier_cost` — and
so the P&L — books. Stored VAT inclusive on `cost_total`, like every courier
invoice, so input VAT is reclaimed the same way.
"""

from __future__ import annotations

from decimal import Decimal

from app.core.money import money
from app.models.delivery_polygon import FulfilmentProviderEnum
from app.models.order import Order
from app.models.order_delivery import OrderDelivery
from app.services.orders.order_pricing import VAT_RATE

__all__ = ["third_party_cost_editable", "third_party_cost_gross"]


def third_party_cost_editable(delivery: OrderDelivery, order: Order | None) -> bool:
    """A third party carried it and it has been delivered.

    `delivered_at` rather than the status, so an order refunded after it was
    delivered still takes the cost of the run that did happen; a cancellation
    that never left the kitchen does not.
    """
    return (
        delivery.provider == FulfilmentProviderEnum.THIRD_PARTY.value
        and order is not None
        and order.delivered_at is not None
    )


def third_party_cost_gross(cost: Decimal | None, vat_inclusive: bool) -> Decimal | None:
    """The amount to store: VAT inclusive, 5% added to a pre-VAT figure."""
    if cost is None:
        return None
    return money(cost if vat_inclusive else cost * (1 + VAT_RATE))
