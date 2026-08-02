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

    #: The same in every zone, on purpose. It costs several times more to reach
    #: Fujairah than Sharjah, but a threshold that moved with the address would
    #: be the one place the delivery map became visible to the customer.
    free_delivery_threshold: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("200.00")
    )
    pickup_fee: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00")
    )
    #: Charged when a pin falls outside every zone on the active map — a real
    #: address we have simply not drawn yet. Quoting nothing would be worse.
    default_delivery_fee: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("50.00"), server_default="50.00"
    )

    def __repr__(self) -> str:
        return f"<DeliverySettings threshold={self.free_delivery_threshold}>"
