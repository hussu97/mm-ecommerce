from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.money import money

from .base import Base, TimestampMixin, UUIDMixin, status_vocabulary

if TYPE_CHECKING:
    from .delivery_polygon import DeliveryPolygon
    from .order_delivery import OrderDelivery

#: Every window is written and read in the shop's own clock. Storing UTC would
#: mean "the 6pm batch" silently moved if the offset ever changed, and would
#: make the admin screen a conversion exercise.
DELIVERY_TIMEZONE = "Asia/Dubai"

#: One pickup plus fifteen drops is Lalamove's ceiling on a single order.
#: A batch larger than this is split across several courier orders.
MAX_DROPS_PER_ORDER = 15


class BatchStatusEnum(str, enum.Enum):
    #: Collecting orders. Nothing has been sent to the courier.
    PENDING = "pending"
    #: A sweep has claimed it and is mid-flight. Exists so a second worker
    #: cannot pick up the same batch between the quote and the booking.
    DISPATCHING = "dispatching"
    DISPATCHED = "dispatched"
    #: The courier refused it. The orders inside are still packed and still
    #: need a driver, so this is a queue of work for a human.
    FAILED = "failed"
    #: Emptied before it fired — every order in it was cancelled or moved.
    CANCELLED = "cancelled"


class DeliveryBatchGroup(Base, UUIDMixin, TimestampMixin):
    """
    A set of zones whose orders ride together, and the schedule they share.

    This is the thing that used to be missing. Windows hung off a single polygon
    and `_open_batch` then merged any batches leaving on the same minute — so
    which zones shared a courier booking was decided by two schedules
    coincidentally ending together. Nobody declared it, nothing displayed it, and
    moving one zone's slot silently re-partitioned another zone's runs.

    Now it is a row. The three Dubai bands are one group because somebody said
    so; the northern city zones are another. A zone in no group dispatches the
    moment it is ready.

    **One courier per group.** A booking is placed with one carrier, so a group
    spanning two of them could never be dispatched as a single order. The
    courier must also have `supports_batching` — a schedule attached to a third
    party is a promise about a van we do not control.
    """

    __tablename__ = "delivery_batch_groups"

    #: What the shop calls it — "Dubai", "Northern Emirates".
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    #: `couriers.code`. Not a foreign key to `couriers.id` because every other
    #: reference to a carrier in this schema is by code, and one join key beats
    #: two.
    courier_code: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("couriers.code", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    #: How long after the run leaves that the last box on it is through a door.
    #:
    #: Per group rather than one constant, because it is a property of the route
    #: and not of the courier: the Dubai run is 90 minutes and the northern one
    #: is 120, on the same rate card, because one crosses three emirates. One
    #: number for every drop on the route — which stop is last is not knowable
    #: when the promise is made, since the courier optimises the order after we
    #: hand it over.
    delivery_minutes_after_dispatch: Mapped[int] = mapped_column(
        Integer, nullable=False, default=90, server_default="90"
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    polygons: Mapped[list[DeliveryPolygon]] = relationship(
        "DeliveryPolygon", back_populates="batch_group"
    )
    windows: Mapped[list[DeliveryBatchWindow]] = relationship(
        "DeliveryBatchWindow",
        back_populates="group",
        cascade="all, delete-orphan",
        order_by="DeliveryBatchWindow.start_hour, DeliveryBatchWindow.start_minute",
    )

    def __repr__(self) -> str:
        return f"<DeliveryBatchGroup {self.name} via {self.courier_code}>"


class DeliveryBatchWindow(Base, UUIDMixin, TimestampMixin):
    """
    A slot of the day whose orders travel together.

    An order that becomes dispatchable inside this window waits for the window
    to end, and then leaves with everything else that accumulated in it. That
    wait is the entire point: one route carrying five drops costs about a third
    per delivery of five separate ones, and that difference is what makes the
    website beat the aggregators.

    Windows hang off a **group**, not a zone. Density is local — Sharjah fills a
    van in an hour, Abu Dhabi would never fill one at all — but it is local to a
    set of zones that share a run, not to one shape on a map. Three Dubai bands
    fill one van between them; giving each its own schedule and hoping the end
    times matched is how they used to end up on the same booking.

    Unlike fees, windows are edited in place rather than through a draft. A
    wrong fee overcharges a customer and cannot be taken back; a wrong window
    delays a dispatch by an hour and is fixed by moving it, with anything still
    waiting rescheduled onto the corrected slot.
    """

    __tablename__ = "delivery_batch_windows"
    __table_args__ = (
        CheckConstraint(
            "start_hour BETWEEN 0 AND 23 AND end_hour BETWEEN 0 AND 24",
            name="ck_batch_window_hours",
        ),
        CheckConstraint(
            "start_minute BETWEEN 0 AND 59 AND end_minute BETWEEN 0 AND 59",
            name="ck_batch_window_minutes",
        ),
    )

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("delivery_batch_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: What the shop calls it — "Batch 3", "Evening run".
    label: Mapped[str] = mapped_column(String(60), nullable=False)

    start_hour: Mapped[int] = mapped_column(Integer, nullable=False)
    start_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Hour 24 means midnight at the end of the day, so a 23:00–24:00 window can
    #: be written without pretending it wraps.
    end_hour: Mapped[int] = mapped_column(Integer, nullable=False)
    end_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    group: Mapped[DeliveryBatchGroup] = relationship(
        "DeliveryBatchGroup", back_populates="windows"
    )
    batches: Mapped[list[DeliveryBatch]] = relationship(
        "DeliveryBatch", back_populates="window"
    )

    @property
    def start_minutes(self) -> int:
        return self.start_hour * 60 + self.start_minute

    @property
    def end_minutes(self) -> int:
        return self.end_hour * 60 + self.end_minute

    @property
    def wraps_midnight(self) -> bool:
        """A window like 22:00–02:00, which belongs to two calendar days."""
        return self.end_minutes <= self.start_minutes

    def contains_minute(self, minute_of_day: int) -> bool:
        """Half-open: the start belongs to this window, the end to the next."""
        if self.wraps_midnight:
            return (
                minute_of_day >= self.start_minutes or minute_of_day < self.end_minutes
            )
        return self.start_minutes <= minute_of_day < self.end_minutes

    def __repr__(self) -> str:
        return (
            f"<DeliveryBatchWindow {self.label} "
            f"{self.start_hour:02d}:{self.start_minute:02d}–"
            f"{self.end_hour:02d}:{self.end_minute:02d}>"
        )


class DeliveryBatch(Base, UUIDMixin, TimestampMixin):
    """
    One courier order carrying several of ours.

    Holds everything the courier tells us about the run — the id, the optimised
    route, the distance, what it cost — while each `OrderDelivery` in it keeps
    the customer-specific half: which stop was theirs, when it was dropped, and
    the proof.
    """

    __tablename__ = "delivery_batches"
    __table_args__ = (
        # Migration 099. Our own lifecycle only — `courier_status` below is
        # the provider's verbatim word and stays unconstrained by design.
        status_vocabulary("delivery_batches", "status", BatchStatusEnum),
    )

    #: Which group's schedule opened this run. Together with `dispatch_at` it
    #: *is* the run's identity — two groups closing a slot on the same minute now
    #: get two bookings, which is the whole point of the group existing.
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("delivery_batch_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Null once a window is deleted. The batch outlives its schedule because
    #: it may already be on the road.
    window_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("delivery_batch_windows.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: Label copied at creation so a dispatched batch still reads correctly
    #: after its window is renamed or removed.
    window_label: Mapped[str | None] = mapped_column(String(60), nullable=True)

    #: When this batch leaves — the end of its window, in real time.
    dispatch_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=BatchStatusEnum.PENDING.value,
        server_default=BatchStatusEnum.PENDING.value,
        index=True,
    )

    courier_order_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    quotation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    share_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    courier_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    driver_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    driver_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    driver_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    driver_plate: Mapped[str | None] = mapped_column(String(30), nullable=True)

    stop_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    distance_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_total: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    cost_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    price_breakdown: Mapped[Any | None] = mapped_column(JSONB, nullable=True)

    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_payload: Mapped[Any | None] = mapped_column(JSONB, nullable=True)

    #: How many times we have tried to book this run. Counts the scheduled
    #: attempt as well as the retries, so it reads as "tried 3 times".
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: When to try again, or null for a run nothing more will be done to
    #: automatically — either because it went out, or because trying again
    #: cannot change the answer.
    #:
    #: This is the whole of the retry queue. The sweep asks for batches whose
    #: time has come and does not look at status, so whatever writes this column
    #: decides what comes back.
    #:
    #: Indexed in migration 060 rather than here, because the index is partial —
    #: all but a handful of rows are null forever and only the ones owing an
    #: attempt are worth carrying.
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    window: Mapped[DeliveryBatchWindow | None] = relationship(
        "DeliveryBatchWindow", back_populates="batches"
    )
    deliveries: Mapped[list[OrderDelivery]] = relationship(
        "OrderDelivery", back_populates="batch"
    )

    @property
    def is_open(self) -> bool:
        """Still collecting — orders can join and the schedule can move it."""
        return self.status == BatchStatusEnum.PENDING.value

    @property
    def cost_per_delivery(self) -> Decimal | None:
        if self.cost_total is None or not self.stop_count:
            return None
        return money(Decimal(str(self.cost_total)) / self.stop_count)

    def __repr__(self) -> str:
        return (
            f"<DeliveryBatch {self.window_label or '-'} "
            f"{self.dispatch_at:%Y-%m-%d %H:%M} {self.status} x{self.stop_count}>"
        )
