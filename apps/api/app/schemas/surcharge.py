"""One fee the customer paid on top of the goods, as a screen or a receipt
lists it. Built from `services/orders/order_surcharges`, the one registry of
those fees, so a new one reaches every reader without a schema change."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.services.orders import order_surcharges


class SurchargeLine(BaseModel):
    #: The `orders` column it came from (`low_order_fee`, `delivery_fee`) —
    #: stable, for a client to key on. The label is for display only.
    code: str
    label: str
    amount: float

    @classmethod
    def _of(cls, lines: list[order_surcharges.Surcharge]) -> list["SurchargeLine"]:
        return [cls(code=s.code, label=s.label, amount=float(s.amount)) for s in lines]

    @classmethod
    def surcharges_of(cls, order: Any) -> list["SurchargeLine"]:
        """The surcharges alone — delivery is not among them."""
        return [] if order is None else cls._of(order_surcharges.surcharges(order))

    @classmethod
    def fees_of(cls, order: Any) -> list["SurchargeLine"]:
        """The delivery (or pickup) fee, then every surcharge — what a receipt
        prints between the discount and the total."""
        return [] if order is None else cls._of(order_surcharges.fee_lines(order))
