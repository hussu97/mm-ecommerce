"""
The fees a customer paid on top of the goods and the delivery fee.

One list, so a new fee is added in one place and every screen that has to
account for it — the fulfilment card's margin, the reassignment quote, the
printed ticket — picks it up without somebody remembering to visit each.

**Why delivery is not in it.** `orders.delivery_fee` is the price of the run and
is shown beside the courier's bill on its own line; these are the charges that
ride alongside it. The fulfilment margin counts both, because a small-basket fee
exists to pay for a delivery the basket is too small to carry — leaving it out
showed a 15-dirham run on a 45-dirham courier as a 30-dirham loss when the
customer had in fact paid 30.

Only non-zero fees are returned: a ticket that prints "Small order fee 0.00" on
every order above the threshold is noise, and a margin is unchanged by a zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, literal_column

from app.core.money import to_decimal

__all__ = [
    "COLUMNS",
    "Surcharge",
    "fee_lines",
    "sql_total",
    "surcharges",
    "surcharges_total",
]

_ZERO = Decimal("0")

#: `(column on orders, label)`. Adding a fee is a column and a line here.
_FEES: tuple[tuple[str, str], ...] = (("low_order_fee", "Small order fee"),)

#: The `orders` columns, for a caller that sums them in SQL.
COLUMNS: tuple[str, ...] = tuple(column for column, _ in _FEES)


def _amount(value: Any) -> Decimal:
    """A money column's value, or zero for anything that is not a number — an
    unloaded or absent column must cost a missing line, not a 500 on a
    receipt."""
    if isinstance(value, (Decimal, int, float)) and not isinstance(value, bool):
        return to_decimal(value)
    return _ZERO


@dataclass(frozen=True)
class Surcharge:
    #: The `orders` column it came from — stable, for a client to key on.
    code: str
    label: str
    amount: Decimal


def surcharges(order: Any) -> list[Surcharge]:
    """The non-zero fees on *order*, in the order they are listed above."""
    out: list[Surcharge] = []
    for column, label in _FEES:
        amount = _amount(getattr(order, column, None))
        if amount > _ZERO:
            out.append(Surcharge(code=column, label=label, amount=amount))
    return out


def fee_lines(order: Any) -> list[Surcharge]:
    """Every fee on *order* above the goods, as a receipt lists them: the
    delivery (or pickup) fee first, then the surcharges. Non-zero only."""
    out: list[Surcharge] = []
    delivery = _amount(getattr(order, "delivery_fee", None))
    if delivery > _ZERO:
        method = getattr(order, "delivery_method", None)
        pickup = getattr(method, "value", method) == "pickup"
        out.append(
            Surcharge(
                code="delivery_fee",
                label="Pickup fee" if pickup else "Delivery fee",
                amount=delivery,
            )
        )
    return out + surcharges(order)


def surcharges_total(order: Any) -> Decimal:
    return sum((s.amount for s in surcharges(order)), _ZERO)


def sql_total(model: Any):
    """The same sum as `surcharges_total`, as a SQL expression over *model*
    (`Order`, or an alias of it) — for the reports that aggregate in the query.

    No bind parameter for the zero: asyncpg cannot type an untyped one added to
    a numeric column."""
    expr = literal_column("0")
    for column in COLUMNS:
        expr = expr + func.coalesce(getattr(model, column), 0)
    return expr
