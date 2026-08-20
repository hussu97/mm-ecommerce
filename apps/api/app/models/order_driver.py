"""
Every driver who has ever held an order, one row each, one of them current.

A courier swapping riders mid-booking is routine — a shift ending, a bike that
will not start, a job passed on — and until this table existed the swap was
applied to nothing. `order_deliveries` had a single set of driver columns and
the code that wrote them ran only when they were empty, so the shop kept the
first name and the first number and rang somebody who had dropped the job an
hour earlier.

**Why a table and not a JSON column on the delivery.** History squashed into
`previous_drivers JSONB` would have recorded the same facts and been unable to
enforce the one that matters: the database could not have answered "who is
carrying order 4 right now", could not have indexed it, and nothing could have
stopped two rows both claiming to be current. Here that is a constraint.

**The one-active-driver rule.** `is_active` is `TRUE` on the current stint and
**NULL** — never `FALSE` — on every past one, under
`UNIQUE (order_id, is_active)`. Postgres treats NULLs in a unique index as
distinct from one another, so any number of finished stints coexist while a
second live one is refused by the database rather than by whoever remembered to
check. A CHECK keeps `FALSE` out, because a `FALSE` row would collide with every
other `FALSE` row for the order and turn "this driver is finished" into an error.

The uniqueness is on `(order_id, is_active)` and deliberately **not** on
`(order_id, driver, is_active)`. Including the driver would allow two *different*
people to be active on one order at the same time, which is the exact thing the
constraint exists to prevent; what it would buy instead — refusing the same
person twice — is not a real failure mode, and a driver genuinely handed an
order back after losing it is a second stint that should have its own row.

`order_deliveries` keeps a copy of the active row's name, number, plate and
position. That is a live snapshot rather than a second opinion — one function,
`driver_assignment.record`, writes both in one transaction — and it exists so
the hot readers (the register's order list, the tracking webhook that fires
every fifteen seconds) do not join to find out who is coming. It is the same
arrangement `orders.status` has with `order_status_events`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .order import Order

__all__ = ["OrderDriver"]


class OrderDriver(Base, UUIDMixin, TimestampMixin):
    """One driver's stint on one order."""

    __tablename__ = "order_drivers"
    __table_args__ = (
        UniqueConstraint("order_id", "is_active", name="uq_order_driver_active"),
        CheckConstraint(
            "is_active IS NULL OR is_active IS TRUE",
            name="ck_order_drivers_active_true_or_null",
        ),
        # The ledger is read in stint order — "who had it, in what order" — and
        # written by looking up the highest sequence so far.
        Index("ix_order_drivers_order_sequence", "order_id", "sequence"),
    )

    #: Keyed to the order rather than the booking, because that is the thing a
    #: person asks about and because a booking is not stable: Lalamove cancels
    #: and clones one under a new id to adjust it (`ORDER_REPLACED`), and a
    #: re-dispatch makes another. The driver who collected order 4 collected
    #: order 4 whichever booking id was live at the time. Which booking it was
    #: is kept below, as a fact about the stint rather than as its identity.
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Which courier reported this person — `lalamove` or `noon_send`. Their
    #: word, unconstrained, for the same reason `courier_status` is.
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The booking that was live when they took it.
    courier_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ── who ───────────────────────────────────────────────────────────────────
    #
    # `courier_driver_id` rather than `driver_id`: this row *is* a driver, and a
    # column called `driver_id` on it reads as a foreign key to some other
    # driver. It is the courier's own handle, and only Lalamove issues one —
    # noon Send's `da_details` is a name, a number and a position with no id
    # anywhere, which is why identity has to fall back to the phone.
    courier_driver_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    plate: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # ── where ─────────────────────────────────────────────────────────────────
    #
    # Per stint rather than per order: a position is a fact about a person, and
    # carrying the last driver's pin onto the next one would put a stranger's
    # distance on the counter's screen.
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    #: When that pair was true. Without it a position from twenty minutes ago is
    #: indistinguishable from one from twenty seconds ago.
    located_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── when ──────────────────────────────────────────────────────────────────
    #: 1-based. Which stint this is, and the number the register compares against
    #: its own ledger to decide a driver slip is owed. A counter rather than a
    #: timestamp precisely because two terminals must not be able to read it
    #: differently, and because it cannot go backwards.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When somebody else took over. Null on the active row, set on every other.
    replaced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: `True` on exactly one row per order, `NULL` on the rest. Never `False` —
    #: see the module docstring.
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    order: Mapped[Order] = relationship("Order", back_populates="drivers")

    def __repr__(self) -> str:
        return (
            f"<OrderDriver #{self.sequence} {self.name or self.courier_driver_id or '?'}"
            f"{' (current)' if self.is_active else ''}>"
        )
