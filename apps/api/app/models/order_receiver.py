"""
Who a delivery is for, when that is not the person who ordered it.

A customer buying a cake for someone else gives us two people: themselves, who
pays and whom the coupon rules and the confirmation email are about, and the
recipient, who the courier actually hands the box to. The orderer's own name and
number stay on the order (`customer_name` / `customer_phone`) — that is the
identity the per-customer coupon limits key on, so a gift never spends the
recipient's allowance. The recipient lives here, one row per order, read by
`address_format.delivery_contact` when a courier drop-off is built.

Checkout-level, not address-level, deliberately: it is a property of *this
order* ("send this one to my mother"), not of a saved address in the book, and a
saved address is shared across orders where this is not. One-to-one with the
order, cascade-deleted with it. No phone verification — the recipient never
signs in; the number is only ever dialled by a driver.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .order import Order


class OrderReceiver(Base, UUIDMixin, TimestampMixin):
    """The gift recipient on one order, when it was placed for someone else."""

    __tablename__ = "order_receivers"

    #: One receiver per order, cascade-deleted with it. Unique so the one-to-one
    #: is a database fact rather than a convention the code has to keep.
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    #: The recipient's name, as the courier should address the drop-off.
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    #: E.164 where it could be parsed, the given string otherwise — the same
    #: policy `Order.customer_phone` follows, set through `describe_phone` on the
    #: one write path (`order_service._persist_order`). A number the driver has
    #: to ring is kept even when it cannot be normalised.
    phone: Mapped[str] = mapped_column(String(30), nullable=False)
    #: ISO region ("AE", "GB") and line type ("mobile"/"landline"/…), beside the
    #: number for readability, filled from the same parse. Null when the number
    #: could not be parsed.
    phone_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    phone_type: Mapped[str | None] = mapped_column(String(20), nullable=True)

    order: Mapped[Order] = relationship("Order", back_populates="receiver")

    def __repr__(self) -> str:
        return f"<OrderReceiver {self.name} {self.phone}>"
