from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Numeric
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class DeliverySettings(Base, UUIDMixin, TimestampMixin):
    """
    The three delivery numbers that are not a property of any zone.

    Expected to have exactly one row. It used to live beside the `Region` model
    and was the only part of that file worth keeping: the threshold is
    deliberately the same everywhere, pickup has no zone at all, and the
    default is what a pin gets when it falls outside every shape on the map.
    """

    __tablename__ = "delivery_settings"

    #: One number for the whole country — a threshold that moved with the
    #: address would be the one place the delivery map became visible to the
    #: customer. *Whether* it applies is a different question, and one the zone
    #: answers: free delivery only reaches the zones we price ourselves, because
    #: outside them there is no fee of ours to waive, only a courier bill that
    #: does not shrink when the basket grows.
    free_delivery_threshold: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("150.00")
    )
    pickup_fee: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00")
    )
    #: Charged when a pin falls outside every zone on the active map — a real
    #: address we have simply not drawn yet. Quoting nothing would be worse.
    default_delivery_fee: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("50.00"), server_default="50.00"
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
        return f"<DeliverySettings threshold={self.free_delivery_threshold}>"
