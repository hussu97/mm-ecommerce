"""
A marketplace order that is coming back to the shop.

Talabat cancels some orders after its rider has already collected them — the
customer could not be found, the address was wrong, the rider was too late — and
the rider then brings the box back. To hand it over the rider must be given a
**return PIN**, which Talabat shows only in its partner portal (GrubOps never
carries it). This row is where MM keeps that PIN and whether the box has come
back, so the register can show it against the order, print a docket for the
kitchen, and record the moment somebody took the box back in.

One row per order, cascade-deleted with it. `status` is an internal lifecycle
(canon rule 6: String + CHECK):

- `pin_pending` — MM knows the order was cancelled after it left the kitchen,
  but has not read the PIN from the portal yet.
- `awaiting_return` — the PIN is known and the rider is on the way back.
- `received` — somebody on the register marked the box received back.
- `not_returning` — the portal says the rider never collected it (nothing is
  coming back), or it was delivered and cancelled afterwards (the customer kept
  it). Kept rather than deleted so a later re-read cannot re-open it by
  accident, and so the PIN is still on record.

`cancel_owner` / `cancel_reason` are the marketplace's own words (provider
verbatim, unconstrained). Talabat mints a PIN on *every* cancelled order, even
one cancelled before a rider ever arrived, so the PIN alone never means a return
— the rider's pickup does (see `aggregators.marketplace_returns`).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .order import Order


class MarketplaceReturnStatusEnum(str, enum.Enum):
    PIN_PENDING = "pin_pending"
    AWAITING_RETURN = "awaiting_return"
    RECEIVED = "received"
    NOT_RETURNING = "not_returning"


class MarketplaceReturnPinSourceEnum(str, enum.Enum):
    #: Read on demand the moment the GrubOps cancellation arrived.
    TRIGGER = "trigger"
    #: Read by the hourly sales scrape.
    SCRAPE = "scrape"


class MarketplaceReturn(Base, UUIDMixin, TimestampMixin):
    """The return PIN and hand-back state of one marketplace-cancelled order."""

    __tablename__ = "marketplace_returns"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pin_pending', 'awaiting_return', 'received', 'not_returning')",
            name="ck_marketplace_returns_status",
        ),
        CheckConstraint(
            "pin_source IS NULL OR pin_source IN ('trigger', 'scrape')",
            name="ck_marketplace_returns_pin_source",
        ),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    #: Canonical channel code (`talabat`), as on `orders.aggregator_channel`.
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    #: The marketplace's own order id (Talabat's 10-digit number).
    external_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=MarketplaceReturnStatusEnum.PIN_PENDING.value,
    )
    return_pin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    pin_source: Mapped[str | None] = mapped_column(String(10), nullable=True)
    pin_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Who the marketplace says cancelled (`CUSTOMER`, `TRANSPORT`, `Rider`…) and
    #: why (`UNABLE_TO_FIND`, "Incorrect address"…). Verbatim.
    cancel_owner: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    received_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    received_by_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The RETURN_FROM_ORDERS movement that put the returned goods back on hand,
    #: when receiving it restocked anything (null when inventory was never
    #: consumed, or somebody had already disposed of the cancellation).
    restock_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transactions.id", ondelete="SET NULL"),
        nullable=True,
    )

    order: Mapped[Order] = relationship("Order", back_populates="marketplace_return")

    def __repr__(self) -> str:
        return f"<MarketplaceReturn {self.external_order_id} {self.status}>"
