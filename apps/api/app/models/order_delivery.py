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
    from .delivery_batch import DeliveryBatch
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


class NoonSendStatusEnum(str, enum.Enum):
    """noon Send's task lifecycle, verbatim, for the same reason as above.

    Their words do not line up with Lalamove's and are not translated into
    them: `undelivered` is a rider who arrived and could not hand the parcel
    over, which is a different problem from a booking nobody accepted, and
    flattening the two into one word would lose the difference exactly where it
    matters.
    """

    CREATED = "created"
    PENDING_ASSIGNMENT = "pending_assignment"
    ASSIGNED = "assigned"
    ARRIVED_AT_PICKUP_LOCATION = "arrived_at_pickup_location"
    PICKED_UP = "picked_up"
    ARRIVED_AT_DELIVERY = "arrived_at_delivery"
    DELIVERED = "delivered"
    UNDELIVERED = "undelivered"
    CANCELLED = "cancelled"


NOON_SEND_TERMINAL_STATUSES = frozenset(
    {
        NoonSendStatusEnum.DELIVERED.value,
        NoonSendStatusEnum.UNDELIVERED.value,
        NoonSendStatusEnum.CANCELLED.value,
    }
)

#: How far through the journey each status is. Used to refuse a push that would
#: walk a task backwards — their status webhook carries no usable timestamp
#: (`order_nr`, `status_code` and `order_reference` are the whole contract), so
#: the ordering has to come from the words themselves rather than from a clock.
#: Anything unlisted ranks -1 and can never displace a status we already have.
NOON_SEND_STATUS_RANK: dict[str, int] = {
    NoonSendStatusEnum.CREATED.value: 0,
    NoonSendStatusEnum.PENDING_ASSIGNMENT.value: 1,
    NoonSendStatusEnum.ASSIGNED.value: 2,
    NoonSendStatusEnum.ARRIVED_AT_PICKUP_LOCATION.value: 3,
    NoonSendStatusEnum.PICKED_UP.value: 4,
    NoonSendStatusEnum.ARRIVED_AT_DELIVERY.value: 5,
    # The three ways it ends all sit at the top: none of them may be undone by
    # a late push describing something that happened earlier.
    NoonSendStatusEnum.DELIVERED.value: 6,
    NoonSendStatusEnum.UNDELIVERED.value: 6,
    NoonSendStatusEnum.CANCELLED.value: 6,
}


NOON_SEND_FAILED_STATUSES = frozenset(
    {
        NoonSendStatusEnum.UNDELIVERED.value,
        NoonSendStatusEnum.CANCELLED.value,
    }
)


class SliderStatusEnum(str, enum.Enum):
    """Slider's delivery lifecycle, verbatim, for the same reason as above.

    Linear, and that matters more here than for the other two: their webhook
    carries no event id and gives no ordering guarantee, so the only thing that
    can stop a late `picked_up` walking a delivered order backwards is the
    order of these words. See `SLIDER_STATUS_RANK`.

    `return_trip_started` is their `undelivered`: a rider arrived, could not
    hand the parcel over, and is bringing it back. It is kept as their word
    rather than translated, because "the rider is carrying it back to the shop"
    and "nobody ever accepted the booking" want different things done about
    them and one word for both loses the difference.
    """

    SEARCHING_RIDER = "searching_rider"
    RIDER_ASSIGNED = "rider_assigned"
    HEADING_TO_PICKUP = "heading_to_pickup"
    AT_PICKUP = "at_pickup"
    PICKED_UP = "picked_up"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    RETURN_TRIP_STARTED = "return_trip_started"
    CANCELLED = "cancelled"


SLIDER_TERMINAL_STATUSES = frozenset(
    {
        SliderStatusEnum.DELIVERED.value,
        SliderStatusEnum.RETURN_TRIP_STARTED.value,
        SliderStatusEnum.CANCELLED.value,
    }
)

#: How far through the journey each status is.
#:
#: The only ordering there is. Slider's webhook has no event id and no promise
#: that pushes arrive in order, and the payload's timestamp is theirs rather
#: than ours — so a clock comparison would be trusting a field to decide whether
#: to trust the payload it came in. Anything unlisted ranks -1 and can never
#: displace a status we already hold.
SLIDER_STATUS_RANK: dict[str, int] = {
    SliderStatusEnum.SEARCHING_RIDER.value: 0,
    SliderStatusEnum.RIDER_ASSIGNED.value: 1,
    SliderStatusEnum.HEADING_TO_PICKUP.value: 2,
    SliderStatusEnum.AT_PICKUP.value: 3,
    SliderStatusEnum.PICKED_UP.value: 4,
    SliderStatusEnum.IN_TRANSIT.value: 5,
    # The three ways it ends sit together at the top: none of them may be undone
    # by a late push describing something that happened earlier.
    SliderStatusEnum.DELIVERED.value: 6,
    SliderStatusEnum.RETURN_TRIP_STARTED.value: 6,
    SliderStatusEnum.CANCELLED.value: 6,
}

#: Terminal, and the parcel never reached the customer. Both need a human: the
#: order is paid and boxed with either nobody coming for it or a rider bringing
#: it back.
SLIDER_FAILED_STATUSES = frozenset(
    {
        SliderStatusEnum.RETURN_TRIP_STARTED.value,
        SliderStatusEnum.CANCELLED.value,
    }
)


#: provider -> (terminal statuses, failed statuses). Third-party deliveries have
#: no courier status at all, so they match neither and fall through to empty.
_STATUS_SETS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "lalamove": (TERMINAL_COURIER_STATUSES, FAILED_COURIER_STATUSES),
    "noon_send": (NOON_SEND_TERMINAL_STATUSES, NOON_SEND_FAILED_STATUSES),
    "slider": (SLIDER_TERMINAL_STATUSES, SLIDER_FAILED_STATUSES),
}


def _status_family(provider: str | None) -> str:
    """The courier whose status vocabulary a provider speaks.

    Slider's bike and car are two providers (`slider_bike`, `slider_car`) but one
    courier with one set of statuses, so both resolve to `slider` for a status
    lookup. Everything else is itself.
    """
    if provider in ("slider_bike", "slider_car"):
        return "slider"
    return provider or ""


def is_terminal(provider: str | None, status: str | None) -> bool:
    """Nothing more will happen to this booking on the courier's side."""
    return (
        bool(status)
        and status in _STATUS_SETS.get(_status_family(provider), (frozenset(),) * 2)[0]
    )


#: provider -> the statuses in which the parcel is already on the bike.
#:
#: Read by the one thing that cares: how far the driver is from the *kitchen*.
#: That number answers "how long until somebody collects this" and stops meaning
#: anything the moment they have, because from then on the driver is supposed to
#: be getting further away. Quoting it past collection would put a growing
#: distance on a counter screen next to an order nobody there is waiting for.
_COLLECTED_STATUSES: dict[str, frozenset[str]] = {
    "lalamove": frozenset(
        {
            CourierStatusEnum.PICKED_UP.value,
            CourierStatusEnum.COMPLETED.value,
        }
    ),
    "noon_send": frozenset(
        {
            NoonSendStatusEnum.PICKED_UP.value,
            NoonSendStatusEnum.ARRIVED_AT_DELIVERY.value,
            NoonSendStatusEnum.DELIVERED.value,
        }
    ),
    # `return_trip_started` is deliberately absent. The parcel is on the bike,
    # but the bike is on its way back *here* — so the one question this set
    # answers, "how long until somebody collects this", becomes answerable
    # again rather than meaningless.
    "slider": frozenset(
        {
            SliderStatusEnum.PICKED_UP.value,
            SliderStatusEnum.IN_TRANSIT.value,
            SliderStatusEnum.DELIVERED.value,
        }
    ),
}


def is_collected(provider: str | None, status: str | None) -> bool:
    """The driver has the parcel; they are no longer coming to the kitchen."""
    return bool(status) and status in _COLLECTED_STATUSES.get(
        _status_family(provider), frozenset()
    )


def is_failed(provider: str | None, status: str | None) -> bool:
    """Terminal, and the parcel never reached the customer.

    Read by code that does not know or care which courier it is holding — the
    question "may this order be dispatched again?" has the same answer either
    way, it is only the vocabulary that differs.
    """
    return (
        bool(status)
        and status in _STATUS_SETS.get(_status_family(provider), (frozenset(),) * 2)[1]
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
    #: Who the zone said would carry this, if that is no longer who is carrying
    #: it. Null on the overwhelming majority of orders, which go out with the
    #: courier they were priced against.
    #:
    #: Set when an admin moves a packed third-party order onto Lalamove. The
    #: column above is the live answer and every dispatch path keys off it; this
    #: is the record that the answer changed, which `provider` alone cannot hold
    #: — once flipped, a reassigned order is indistinguishable from one that was
    #: always Lalamove, and the two want different promises kept and different
    #: things said on the admin card.
    original_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: The polygon that priced this order, by name, at the time it was placed.
    #: A snapshot: the map is versioned and the zone may be redrawn tomorrow.
    zone_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: The row behind that name, so batching can find the zone's schedule.
    #: Nulled rather than cascaded if the map version is deleted — the name
    #: above is the part that has to survive.
    polygon_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("delivery_polygons.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
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

    # ── batching ──────────────────────────────────────────────────────────────
    #: The run this order is travelling on, if it is sharing one. Null means it
    #: went alone — either its zone has no schedule, or nothing covered the
    #: moment it was ready.
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("delivery_batches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: When the order became something a driver could be called for. This is the
    #: moment a window is matched against, and it is now **acceptance**, not
    #: packing.
    #:
    #: It used to be the moment the box was finished, because that was the only
    #: event the shop published. Calling the driver then meant the prep time and
    #: the driver's travel time ran end to end instead of overlapping, and the
    #: press that produced the event was a person being interrupted to state
    #: something the register already knew. Acceptance is the same fact early
    #: enough to be useful: the kitchen has committed to making it.
    #:
    #: The column keeps its name rather than being renamed to `accepted_at` —
    #: `reschedule_group` re-derives every waiting order's window from it, and
    #: what that code needs is "the moment this order entered the queue",
    #: which is exactly what it still holds.
    dispatchable_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Position in the courier's optimised route, 1-based. Which drop is whose.
    stop_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Lalamove's id for this order's own stop, so a per-stop POD update can be
    #: matched to the right customer rather than the whole van.
    stop_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ── the booking ───────────────────────────────────────────────────────────
    #: A seven-digit number a driver can read back down a phone, unique across
    #: every delivery we have ever booked. `MM-20260805-007` is a fine key and a
    #: poor thing to say out loud, and noon Send asked for something shorter so
    #: their riders can quote it. Assigned at dispatch, kept across a re-dispatch,
    #: and null on a third-party zone that never meets a courier. See
    #: `app/services/courier_reference.py`.
    courier_reference: Mapped[str | None] = mapped_column(
        String(7), nullable=True, unique=True, index=True
    )
    courier_order_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    #: Earlier bookings for this same order, oldest first, kept when a failed
    #: dispatch is retried.
    previous_courier_order_ids: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    #: The provider's word, verbatim, and deliberately *not* CHECK-constrained
    #: (migration 099 constrains internal lifecycles only): this column records
    #: somebody else's vocabulary, and Lalamove inventing a status must degrade
    #: to an unknown string in a column, never a rejected webhook.
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
    #
    # One driver, and the ones before them. A courier swapping riders mid-booking
    # is routine on both integrations — a shift ending, a bike that will not
    # start — and it used to overwrite nothing at all, because the code that
    # filled these ran only when the row had no driver yet. The shop kept the
    # first name and the first number and rang somebody who had dropped the job.
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
    #: When the pair above was actually true.
    #:
    #: Not decoration. A position with no age is a position nothing can refuse:
    #: "the driver is 400 m away" reads the same whether it was measured twenty
    #: seconds or twenty minutes ago, and only one of those is a sentence a
    #: counter should act on. `driver_proximity` quotes no distance without this.
    driver_location_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When the *current* driver took the booking.
    driver_assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The driven route from the position above to the kitchen, as Mapbox
    #: measured it against traffic at `driver_route_at`.
    #:
    #: Cached rather than asked per render: the register polls every twenty
    #: seconds, the admin every thirty, and several terminals watch one branch —
    #: so a single order would otherwise put a hundred calls a minute through a
    #: paid API to re-answer a question whose answer changes once.
    #:
    #: Stamped with when the *route* was computed rather than when the position
    #: was reported, because the two go stale for different reasons: Mapbox
    #: unreachable or the token missing leaves a fresh pin with an old route, and
    #: `driver_proximity` has to be able to tell those apart and fall back.
    driver_route_km: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 1), nullable=True
    )
    driver_route_minutes: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1), nullable=True
    )
    driver_route_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: How many drivers this booking has had. 0 until one is matched, 1 for the
    #: ordinary case, 2+ once somebody has been swapped.
    #:
    #: A counter, deliberately, rather than a timestamp or a boolean. The
    #: register prints a slip naming the driver, and it decides a fresh one is
    #: owed by comparing this against what it last printed — so the value has to
    #: be something two terminals cannot read differently and that cannot go
    #: backwards. `driver_assigned_at` is neither: clocks differ, and a courier
    #: re-sending an older push would move it.
    driver_assignment_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: Who had it before is **not** here. It is `order_drivers`, one row per
    #: stint, and the columns above are the live copy of whichever of those rows
    #: is active — the same relationship `orders.status` has with
    #: `order_status_events`. A history squashed into a JSON column on this row
    #: could not have been asked "who is carrying order 4 right now" by the
    #: database, and nothing could have stopped two drivers being current at once.

    # ── proof of delivery ─────────────────────────────────────────────────────
    pod_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pod_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── timeline ──────────────────────────────────────────────────────────────
    #
    # This is the *booking's* timeline, not the order's. `picked_up_at` and
    # `delivered_at` used to sit here too and they did not belong: they were the
    # same two moments `order_status_events` records as `out_for_delivery` and
    # `delivered`, written by the same webhook a few lines apart, and read by
    # different screens — so the customer's timeline and the admin's card were
    # answering one question from two columns that were free to drift.
    # `fulfilment_service.reached_at` is where both read from now.
    #
    # The three below stay because none of them is an order status. A booking is
    # accepted, and separately called off — and a cancelled *booking* is routine
    # on an order that is carrying on perfectly well, which is exactly why it
    # cannot be folded into the order's own history.
    booked_at: Mapped[datetime | None] = mapped_column(
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

    # ── retry ─────────────────────────────────────────────────────────────────
    #
    # Deliberately the same two columns, with the same names and the same
    # meaning, as `delivery_batches`. Both paths answer "when do we try this
    # again"; giving each its own vocabulary is how they would come to answer it
    # differently.
    #
    # Before these existed, a failure on the un-batched path was terminal in
    # everything but name: `dispatch_due_batches` sweeps batches, an order that
    # went alone is in no batch, and the `packed` transition that first tried it
    # had already happened. It waited for somebody to notice a red box on an
    # admin screen.
    #
    #: How many times a driver has been asked for and refused us. Zero on an
    #: order that has never failed, which is almost all of them.
    dispatch_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: When the sweep should try again, or null for "nothing will happen on its
    #: own" — which covers both an order that never failed and one that has run
    #: out of rungs and now needs a person.
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    order: Mapped[Order] = relationship("Order", back_populates="delivery")
    batch: Mapped[DeliveryBatch | None] = relationship(
        "DeliveryBatch", back_populates="deliveries"
    )

    @property
    def is_booked(self) -> bool:
        return bool(self.courier_order_id)

    @property
    def was_reassigned(self) -> bool:
        """This order is travelling with a courier its zone did not choose."""
        return bool(self.original_provider) and self.original_provider != self.provider

    @property
    def has_swapped_drivers(self) -> bool:
        """More than one person has held this booking."""
        return (self.driver_assignment_count or 0) > 1

    @property
    def is_driver_on_the_way_here(self) -> bool:
        """
        A named driver is still travelling towards the kitchen.

        Both endings are excluded and for different reasons: once the parcel is
        collected the driver is meant to be getting further away, and once the
        booking is over — cancelled, rejected, expired — there is no driver at
        all, whatever name the row is still carrying.
        """
        if not (self.driver_id or self.driver_name):
            return False
        return not is_collected(self.provider, self.courier_status) and not is_terminal(
            self.provider, self.courier_status
        )

    @property
    def is_waiting_for_a_batch(self) -> bool:
        """Assigned to a run that has not left yet."""
        return bool(self.batch_id) and not self.courier_order_id

    @property
    def is_awaiting_retry(self) -> bool:
        """The sweep will ask for a driver again without anybody doing anything."""
        return self.next_attempt_at is not None and not self.courier_order_id

    @property
    def needs_attention(self) -> bool:
        """
        A paid order with no one coming for it *and* nothing that will change that.

        An order the sweep is still working through is not on this list. It has a
        `last_error` — every attempt does — but a human acting on it would only
        be racing the retry that is already scheduled, and a queue that fills up
        with rows resolving themselves is a queue people stop reading. It
        reappears the moment the ladder runs out, which is exactly when a person
        is the only thing left.
        """
        if self.is_awaiting_retry:
            return False
        return bool(self.last_error) or is_failed(self.provider, self.courier_status)

    def __repr__(self) -> str:
        return (
            f"<OrderDelivery {self.provider} "
            f"{self.courier_order_id or '(unbooked)'} {self.courier_status or '-'}>"
        )
