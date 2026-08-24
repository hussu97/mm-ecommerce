"""
One precision and one rounding mode for every figure the shop quotes.

There were eight private helpers under five names doing this, and they did not
agree. `pos_pricing.money`, `order_economics._round` and `order_fees._round`
rounded half away from zero; `pos_reports_service._q` (now `pos_reports`), `till_service._q` and
`grubops_orders_service._q2` called `.quantize()` with no `rounding=` and got
Python's default, which is bankers' rounding — half to even.

Both were applied to the same POS money. A line that prices at 0.125 becomes
0.13 on the pricing path and 0.12 on the report of that same sale, and the
difference only ever shows up as a report that disagrees with the till by a
fils and no explanation of which one is right.

`ROUND_HALF_UP` is the one to keep. It is what the pricing path already used,
what a customer expects from a printed total, and what a human doing the sum
by hand produces. Bankers' rounding is the better choice for long chains of
statistics and the wrong one for a receipt.

Values arrive from SQLAlchemy `Numeric`, from JSON, and from provider payloads
as `str`, so everything goes through `Decimal(str(...))` rather than
`Decimal(float)` — binary floats do not have the value you typed.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

__all__ = [
    "CENTS",
    "COST",
    "QUANTITY",
    "ZERO",
    "money",
    "money_or_none",
    "quantity",
    "to_decimal",
    "unit_cost",
]

#: Two places: every customer-facing figure and every till total.
CENTS = Decimal("0.01")
#: Four places: stock counts, which are weighed as well as counted.
QUANTITY = Decimal("0.0001")
#: Six places: a per-unit ingredient cost, where two places rounds a gram of
#: vanilla to nothing and the error compounds over a batch.
COST = Decimal("0.000001")

ZERO = Decimal("0.00")


def to_decimal(value: object) -> Decimal:
    """
    A `Decimal` of whatever arrived, treating `None` and `""` as zero.

    Not quantised: use it when the figure is about to be summed or multiplied
    and only the result should be rounded. Rounding each term first is how a
    total ends up a fils off its own lines.
    """
    if value is None or value == "":
        return Decimal(0)
    return Decimal(str(value))


def money(value: object) -> Decimal:
    """Quantise to two places, rounding half away from zero."""
    return to_decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def quantity(value: object) -> Decimal:
    """Quantise a stock figure to four places."""
    return to_decimal(value).quantize(QUANTITY, rounding=ROUND_HALF_UP)


def unit_cost(value: object) -> Decimal:
    """Quantise a per-unit cost to six places."""
    return to_decimal(value).quantize(COST, rounding=ROUND_HALF_UP)


def money_or_none(value: object) -> Decimal | None:
    """
    `money()` for a provider field that is allowed to be absent.

    Returns `None` for anything that is not a number, rather than raising: the
    caller is reading somebody else's payload and "they did not send a price"
    is an answer, not a failure.
    """
    if value is None or value == "":
        return None
    try:
        return money(value)
    except (InvalidOperation, ValueError, TypeError):
        return None
