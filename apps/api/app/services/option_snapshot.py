"""
One shape for a chosen modifier, whichever checkout wrote it.

`order_items.selected_options_snapshot` is raw JSON, and two places write it:

* `cart_service` for a website order — `option_name`, `option_price`,
  `charged_total`, `option_id`.
* `pos_order_service` for a counter sale — `name`, `price`, `sku`,
  `modifier_option_id`.

Both are reasonable and both are load-bearing where they are: the storefront
prices baskets off `charged_total`, and the emails read `option_name`. Changing
either writer would mean migrating every historic row and breaking the readers
that already depend on it.

What is *not* reasonable is making the register learn both. It did not — it
knows only the counter shape — so a website order with a modifier on it failed
to decode outright, and a failed decode is not a degraded row: it is the whole
response gone. A branch with one flavoured brownie in the day's orders saw an
empty queue and an empty history.

So the register's read boundary normalises. One function, applied where the POS
payload is built, and nothing downstream of it has to know there were ever two.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

__all__ = ["for_register"]


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _one(row: Any) -> dict[str, Any] | None:
    """
    One snapshot row in the shape the register reads, or None if it is not a row.

    Anything unrecognisable is dropped rather than guessed at. A modifier line
    the terminal cannot describe is worse than no line: it prints on a kitchen
    ticket, and a ticket that says the wrong thing is how a customer gets the
    wrong cake.
    """
    if not isinstance(row, dict):
        return None

    # The counter shape uses `name`; the website's uses `option_name`. Neither
    # is a fallback for the other — whichever is present is the option's name.
    name = row.get("name") or row.get("option_name")
    if not name:
        return None

    # Per unit, both sides. `charged_total` is deliberately *not* used here:
    # it is the line total after the group's free allowance, and the register
    # multiplies by quantity itself.
    price = row.get("price")
    if price is None:
        price = row.get("option_price")

    # The website wrote `option_id` before `modifier_option_id` existed.
    option_id = row.get("modifier_option_id") or row.get("option_id")

    return {
        "modifier_option_id": str(option_id) if option_id else None,
        "modifier_id": (str(row["modifier_id"]) if row.get("modifier_id") else None),
        "modifier_name": row.get("modifier_name"),
        "name": str(name),
        "sku": row.get("sku") or row.get("option_sku"),
        "price": float(_decimal(price)),
        # Rows written before quantities existed repeated the option instead
        # and carry no key. One is what they meant.
        "quantity": int(row.get("quantity") or 1),
    }


def for_register(rows: Any) -> list[dict[str, Any]]:
    """Every recognisable option on a line, in the register's shape."""
    if not isinstance(rows, list):
        return []
    return [option for option in (_one(row) for row in rows) if option is not None]
