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

The shape itself now lives in `schemas/option_snapshot.OptionSnapshot` — the
column had two dialects and no contract, which is the worst combination for the
one blob in the codebase that most needed one. Both functions here are that
model; they differ only in what they do with a row it refuses.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.exceptions import BadRequestError
from app.schemas.option_snapshot import OptionSnapshot

__all__ = ["for_register", "validated"]


def _one(row: Any) -> dict[str, Any] | None:
    """
    One snapshot row in the shape the register reads, or None if it is not a row.

    Anything unrecognisable is dropped rather than guessed at. A modifier line
    the terminal cannot describe is worse than no line: it prints on a kitchen
    ticket, and a ticket that says the wrong thing is how a customer gets the
    wrong cake.
    """
    try:
        return OptionSnapshot.model_validate(row).model_dump()
    except ValidationError:
        return None


def for_register(rows: Any) -> list[dict[str, Any]]:
    """Every recognisable option on a line, in the register's shape."""
    if not isinstance(rows, list):
        return []
    return [option for option in (_one(row) for row in rows) if option is not None]


def validated(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    The same contract, enforced loudly, for the moment a snapshot is written.

    Returns *rows unchanged*, deliberately. This is a tripwire, not a
    conversion: the two dialects are what the historic data and the live readers
    already hold, so normalising on write would silently change what the
    storefront prices a basket from. What it guarantees is that whatever goes
    into the column can come back out of it — which is exactly the guarantee the
    register discovered it did not have.

    A failure here means a writer built a row that no reader can decode. That is
    a bug in this codebase rather than bad input from a customer (both writers
    build from `modifier_rules.resolve`), so it should surface at the point of
    the write with the row in the message, instead of being stored and found
    months later by a branch whose order queue has gone blank.
    """
    for index, row in enumerate(rows or []):
        try:
            OptionSnapshot.model_validate(row)
        except ValidationError as exc:
            raise BadRequestError(
                f"Option {index} on this line cannot be stored: {row!r} "
                f"({exc.error_count()} problem(s))"
            ) from exc
    return rows
