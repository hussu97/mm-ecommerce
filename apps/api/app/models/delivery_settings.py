from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Numeric
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class DeliverySettings(Base, UUIDMixin, TimestampMixin):
    """
    The delivery numbers that are genuinely not a property of any zone.

    Expected to have exactly one row, and deliberately short. It used to hold a
    national `free_delivery_threshold` and a `default_delivery_fee`, and both
    were removed once every polygon carried its own: a second source of truth
    for a number the map already answers is a number that is wrong wherever the
    two disagree, and the admin was printing both under "applies to every zone"
    while no zone used either.

    What is left is what has no zone to belong to. Pickup happens at a counter,
    so no polygon is involved. The small-order fee is about the fixed cost of
    baking and boxing an order at all, which does not vary with where it goes —
    the only genuinely national number here.

    A pin outside every polygon is now **unserviceable** rather than charged a
    default. The active map tiles the whole country, so "outside every zone"
    means outside the country, and refusing is more honest than quoting a fee
    for a delivery nobody has worked out how to make.
    """

    __tablename__ = "delivery_settings"

    pickup_fee: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00")
    )
    #: Charged on a delivery order whose goods come to no more than
    #: `low_order_threshold`. Nationwide, including the zones that deliver free —
    #: free delivery is about the courier, and this is about the fixed cost of
    #: baking, boxing and handing over an order at all, which a small basket does
    #: not cover however near the customer lives.
    #:
    #: Zero disables it, and so does a null threshold.
    low_order_fee: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00"), server_default="0.00"
    )
    #: The basket at or below which `low_order_fee` applies. Inclusive: a basket
    #: of exactly this much is a small one. Null disables the fee entirely, which
    #: is what it means everywhere the feature has not been switched on.
    low_order_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )

    def __repr__(self) -> str:
        return f"<DeliverySettings pickup={self.pickup_fee} low_order={self.low_order_fee}>"
