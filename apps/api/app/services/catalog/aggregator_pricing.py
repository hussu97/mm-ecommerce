"""The price a product or option sells at on the marketplaces.

MM keeps one counter price (`Product.base_price`, `ModifierOption.price`) and an
optional **aggregator price** beside each. When the aggregator price is set, the
integrator menu quotes it instead — the Grubtech price tag Foodics pushes to the
Barsha/Sharjah API stores, and the portals MM writes to directly for the
non-Foodics outlets. Null means "the counter price", which is every item that has
not been given one.

Everything that builds, diffs or matches the marketplace menu reads the price
through these two functions, so the sync, the drift report and the name+price
option matcher can never disagree about what the marketplaces should be charging.
The counter, the website and the cost/margin estimates keep reading the counter
price; an aggregator order is priced from its own feed (`promote`), never from
here.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def _dec(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def product_price(product: Any) -> Decimal | None:
    """The product's marketplace price: its aggregator price when set, else its
    counter price. None only when neither is set."""
    agg = _dec(getattr(product, "aggregator_price", None))
    return agg if agg is not None else _dec(getattr(product, "base_price", None))


def option_price(option: Any) -> Decimal | None:
    """The option's marketplace price: its aggregator price when set, else its
    counter price."""
    agg = _dec(getattr(option, "aggregator_price", None))
    return agg if agg is not None else _dec(getattr(option, "price", None))


__all__ = ["option_price", "product_price"]
