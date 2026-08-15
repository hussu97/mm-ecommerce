"""
The contract for `order_items.selected_options_snapshot`.

That column is JSONB, and it is the one piece of stored JSON in this codebase
with **two known wire dialects** — `cart_service` writes `option_name` /
`option_price` for a website order, `pos_order_service` writes `name` / `price`
for a counter sale (both documented at `services/option_snapshot.py`). It was
also the one piece with no contract at all: `list[Any]` in the cart and order
schemas, `list` in the POS schema. A shape with two dialects and no model is a
shape nobody can change safely and nobody can validate.

The cost of that was already paid once. The register knew only the counter
dialect, so a website order with a modifier on it failed to decode — and a
failed decode is not a degraded row, it is the whole response: a branch with one
flavoured brownie in the day's orders saw an empty queue and an empty history.

`OptionSnapshot` below is the single definition of the shape. It reads either
dialect and presents the normalised one, which makes it serve both jobs at once:

* `option_snapshot.for_register()` validates each row and **drops** what will
  not decode — the read boundary, where one bad row must not take the response
  with it.
* `option_snapshot.validated()` validates and **raises** — the write boundary,
  where a malformed blob is a bug to hear about now rather than a row somebody
  finds in six months.

**Follow-up:** `schemas/cart.py:selected_options_snapshot` and
`schemas/order.py:selected_options_snapshot` are still `list[Any]`, and
`schemas/pos_order.py` still `list`. They should take `list[OptionSnapshot]`,
which is what makes the shape visible in the generated OpenAPI and in the
admin's generated types. That is a response-schema change with its own
regeneration step, deliberately not folded into the audit pass that introduced
this file.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, field_validator, model_validator

__all__ = ["OptionSnapshot"]


def _price(value: Any) -> float:
    """
    A number, or zero.

    Never raises. A price that will not parse is a display problem; refusing the
    row over it would drop a modifier off a kitchen ticket, and a ticket missing
    a line is how somebody gets the wrong cake. The row's *identity* — that it
    names an option — is what this model is strict about.
    """
    if value is None:
        return 0.0
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return 0.0


class OptionSnapshot(BaseModel):
    """
    One chosen modifier, in the shape the register reads.

    Fields are strings rather than UUIDs on purpose: web orders written before
    `modifier_option_id` existed carry whatever `option_id` held, and historic
    rows are not all parseable as UUIDs. Refusing them would lose the option
    from a receipt to gain a type — a bad trade for data that is already
    written.
    """

    modifier_option_id: str | None = None
    modifier_id: str | None = None
    modifier_name: str | None = None
    #: The only required field. A modifier line that names nothing cannot be
    #: printed, priced or explained, and is not a line.
    name: str
    sku: str | None = None
    #: Per unit, both dialects. `charged_total` is deliberately not read here:
    #: it is the line total after the group's free allowance, and the register
    #: multiplies by quantity itself.
    price: float = 0.0
    #: Rows written before quantities existed repeated the option instead and
    #: carry no key. One is what they meant.
    quantity: int = 1

    @model_validator(mode="before")
    @classmethod
    def _read_either_dialect(cls, data: Any) -> Any:
        """
        Fold the counter and website spellings into one set of fields.

        Neither dialect is a fallback for the other — whichever key is present
        is the option's name, its price, its id. Both are load-bearing where
        they are: the storefront prices baskets off the website shape and the
        emails read `option_name`, so changing either writer would mean
        migrating every historic row. The reading is what gets unified.
        """
        if not isinstance(data, dict):
            # Left to Pydantic to refuse, so the error names the row rather
            # than this validator.
            return data

        name = data.get("name") or data.get("option_name")
        return {
            **data,
            "name": str(name) if name else name,
            "price": _price(
                data.get("price")
                if data.get("price") is not None
                else data.get("option_price")
            ),
            "modifier_option_id": data.get("modifier_option_id")
            or data.get("option_id"),
            "sku": data.get("sku") or data.get("option_sku"),
            "quantity": data.get("quantity") or 1,
        }

    @field_validator(
        "modifier_option_id", "modifier_id", "modifier_name", "sku", mode="before"
    )
    @classmethod
    def _as_text(cls, value: Any) -> str | None:
        """
        Ids arrive as `uuid.UUID` from a service and as `str` from JSONB.

        Coerced rather than typed as `UUID | str` because the column stores
        whatever was written, and every reader of these fields wants text.
        `mode="before"` because the coercion has to happen instead of the type
        check, not after it has already refused the UUID.
        """
        return None if value is None else str(value)
