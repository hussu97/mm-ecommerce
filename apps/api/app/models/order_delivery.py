from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .order import Order


class CourierStatusEnum(str, enum.Enum):
    """Lalamove's order lifecycle, verbatim.

    Kept as their words rather than translated into ours, because the value we
    store has to be the value that arrived — a webhook that says `EXPIRED` is
    evidence, and paraphrasing it into `cancelled` loses the reason.
    """

    ASSIGNING_DRIVER = "ASSIGNING_DRIVER"
    ON_GOING = "ON_GOING"
    PICKED_UP = "PICKED_UP"
    COMPLETED = "COMPLETED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


#: Nothing more will happen to the booking on Lalamove's side.
TERMINAL_COURIER_STATUSES = frozenset(
    {
        CourierStatusEnum.COMPLETED.value,
        CourierStatusEnum.CANCELED.value,
        CourierStatusEnum.REJECTED.value,
        CourierStatusEnum.EXPIRED.value,
    }
)

#: Terminal, and the parcel never moved. These need a human: the order is paid
#: and packed with no one coming to collect it.
FAILED_COURIER_STATUSES = frozenset(
    {
        CourierStatusEnum.CANCELED.value,
        CourierStatusEnum.REJECTED.value,
        CourierStatusEnum.EXPIRED.value,
    }
)


class OrderDelivery(Base, UUIDMixin, TimestampMixin):
    """
    How one order got to the customer, and what that cost us.

    Exists for every delivery order, including the third-party ones we do not
    book in code — otherwise the only zones with a delivery record would be the
    integrated ones, and "what did fulfilment cost last month" would quietly
    exclude half the country.

    The customer never sees any of this. Which courier carries an order is our
    problem, and the storefront is deliberately not told.
    """

    __tablename__ = "order_deliveries"
    __table_args__ = (
        # One booking per order. A re-dispatch overwrites the row and keeps the
        # previous courier id in `previous_courier_order_ids`, so history is not
        # lost but the current booking is never ambiguous.
        UniqueConstraint("order_id", name="uq_order_delivery_order"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── what we decided at checkout ───────────────────────────────────────────
    provider: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    #: The polygon that priced this order, by name, at the time it was placed.
    #: A snapshot: the map is versioned and the zone may be redrawn tomorrow.
    zone_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fee_charged: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)

    #: What the courier said it would cost, taken at checkout and never shown
    #: to the customer. The gap between this and `fee_charged` is the whole
    #: point of the exercise.
    quoted_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    quoted_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    quoted_distance_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quotation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quoted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── the booking ───────────────────────────────────────────────────────────
    courier_order_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    #: Earlier bookings for this same order, oldest first, kept when a failed
    #: dispatch is retried.
    previous_courier_order_ids: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    courier_status: Mapped[str | None] = mapped_column(
        String(30), nullable=True, index=True
    )
    courier_previous_status: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )
    #: Lalamove's own tracking page. Internal for now — surfacing it would tell
    #: the customer who is carrying the order, which we are not ready to say.
    share_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: What the courier actually charged, once the order exists.
    cost_total: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    price_breakdown: Mapped[Any | None] = mapped_column(JSONB, nullable=True)

    # ── driver ────────────────────────────────────────────────────────────────
    driver_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    driver_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    driver_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    driver_plate: Mapped[str | None] = mapped_column(String(30), nullable=True)
    driver_latitude: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6), nullable=True
    )
    driver_longitude: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6), nullable=True
    )

    # ── proof of delivery ─────────────────────────────────────────────────────
    pod_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pod_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── timeline ──────────────────────────────────────────────────────────────
    booked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    picked_up_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Webhooks are not guaranteed to arrive in order, so an update older than
    #: the one already applied is discarded rather than allowed to rewind the
    #: status.
    status_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    cancel_party: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)

    #: The last payload the courier sent, kept verbatim. Their fields change
    #: without notice and the raw copy is what makes a surprise diagnosable.
    last_payload: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    #: Why the most recent dispatch attempt failed, in plain words, for the
    #: admin who has to decide what to do about it.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    order: Mapped[Order] = relationship("Order", back_populates="delivery")

    @property
    def is_booked(self) -> bool:
        return bool(self.courier_order_id)

    @property
    def needs_attention(self) -> bool:
        """A paid, packed order with no one coming for it."""
        return bool(self.last_error) or (self.courier_status in FAILED_COURIER_STATUSES)

    def __repr__(self) -> str:
        return (
            f"<OrderDelivery {self.provider} "
            f"{self.courier_order_id or '(unbooked)'} {self.courier_status or '-'}>"
        )
