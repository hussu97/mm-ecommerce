"""
When each order reached each status, and who moved it there.

Before this, the only moment an order could prove was the one it was written at.
The admin's timeline read `created_at` for step one and then borrowed three
stamps off `order_deliveries` — `dispatchable_at`, `picked_up_at`,
`delivered_at` — every one of which is courier telemetry. A pickup order, a
third-party zone or an order somebody walked through by hand produces none of
them, so four of the five steps rendered blank and there was nothing in the
database that could have filled them.

`audit_logs` was not the answer either. It carries `STATUS_CHANGE` rows from
exactly one place — the admin status endpoint — so a courier webhook or a
register marking an order packed left no trace at all.

**Coverage here is structural rather than remembered.** When this recorder was
built, `Order.status` was assigned from thirteen places and only two of them
went through `order_service.update_status`; the rest wrote the column directly,
and three did it with a raw string. A recorder each of those has to call is a
recorder that is already incomplete, and would go further out of date with the
next writer. So the transition is captured by a SQLAlchemy attribute listener,
which cannot be bypassed by assigning the column, and the rows are materialised
in `before_flush` where there is a session to add them to.

The writers have since been herded through one door —
`order_lifecycle.transition()` validates every move and carries its
consequences — but the listener deliberately stays. It is the guarantee that
holds even if a fourteenth writer appears, and `order_lifecycle` leans on the
same mechanism to warn when one does.

What the listener cannot know is *who*. That comes from a `ContextVar` the few
entry points that do know set around their work — the admin endpoint, the
register, the payment webhooks, the couriers. Nothing set means `system`, which
is honest: it says the transition happened and we cannot name a hand behind it,
rather than attributing it to whoever happened to be nearby.
"""

from __future__ import annotations

import contextvars
import enum
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterator

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, event, inspect
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from .base import Base, UUIDMixin, utcnow
from .order import Order, OrderStatusEnum

if TYPE_CHECKING:
    from .user import User

__all__ = [
    "OrderStatusEvent",
    "StatusActor",
    "current_actor",
    "StatusSourceEnum",
    "acting_as",
    "pending_events",
]

#: Where a parked transition waits between the attribute being assigned and the
#: next flush. Lives in the instance's own `info` dict rather than in a
#: module-level or context-local collection: a brand-new `Order` has no session
#: and no primary key at the moment its status is first set, so the instance is
#: the only thing that reliably identifies which order the transition belongs to.
_PARKED = "pending_status_events"


class StatusSourceEnum(str, enum.Enum):
    """What kind of thing moved the order.

    Plain text rather than a PostgreSQL enum, for the same reason
    `FulfilmentProviderEnum` is: a new kind of actor should be an insert, not a
    migration that rewrites a type while the admin is reading from it.
    """

    #: The customer completing checkout, or a COD order confirming itself.
    CHECKOUT = "checkout"
    #: A human on the admin order screen.
    ADMIN = "admin"
    #: A register — the counter marking an order packed.
    POS = "pos"
    #: A payment gateway webhook: confirmed, failed, refunded, disputed.
    PAYMENT = "payment"
    #: A courier webhook. `actor_label` carries which one.
    COURIER = "courier"
    #: The GrubOps aggregator ingest loop, moving an order to mirror the state
    #: GrubTech reports. `at` carries GrubOps's own history timestamp.
    AGGREGATOR = "aggregator"
    #: Nobody set a context. The transition is real; the hand behind it is not
    #: recorded, and guessing would be worse than saying so.
    SYSTEM = "system"
    #: Reconstructed by migration 091 from whatever evidence survived. Kept
    #: distinct so nobody mistakes a reconstruction for a contemporaneous record.
    BACKFILL = "backfill"


@dataclass(frozen=True)
class StatusActor:
    """Who is moving orders right now, for the rows written while it is set."""

    source: str
    #: The admin or staff user, when there is one. Null for a courier or a
    #: gateway, which are not users.
    actor_id: uuid.UUID | None = None
    #: An email, a courier code, a gateway name — something readable that
    #: survives the user row being deleted.
    actor_label: str | None = None
    #: Free text for the row: the admin's note, the courier's own status word.
    note: str | None = None
    #: When it happened, if that is not the same as when we heard about it.
    #:
    #: Only a courier webhook passes this, and it matters: their push carries
    #: the moment the rider actually collected, and it can arrive minutes later
    #: — Lalamove retries an unacknowledged event for a full day. Stamping the
    #: history with our processing time would make "picked up at" mean "webhook
    #: handled at", which is a different fact and the wrong one to put on a
    #: customer's timeline.
    #:
    #: Null everywhere else, where the moment we act *is* the moment.
    at: datetime | None = None


_ACTOR: contextvars.ContextVar[StatusActor | None] = contextvars.ContextVar(
    "order_status_actor", default=None
)


@contextmanager
def acting_as(
    source: str,
    *,
    actor_id: uuid.UUID | None = None,
    actor_label: str | None = None,
    note: str | None = None,
    at: datetime | None = None,
) -> Iterator[None]:
    """
    Attribute every status change made inside this block.

    A context manager rather than a parameter threaded through the call chain:
    the writers are spread across services that have no business knowing about
    an audit trail, and the ones that *do* know who is acting are the request
    handlers at the top. Resetting on the way out matters — a `ContextVar` left
    set would attribute the next request's transitions to the previous one's
    admin.
    """
    token = _ACTOR.set(
        StatusActor(
            source=source,
            actor_id=actor_id,
            actor_label=actor_label,
            note=note,
            at=at,
        )
    )
    try:
        yield
    finally:
        _ACTOR.reset(token)


def current_actor() -> "StatusActor":
    """Who is moving orders right now, for a consequence that needs to know.

    The write-back to GrubOps reads this to tell its own mirroring apart from a
    person: an ingest move is attributed `aggregator` and must not be echoed
    back out, where an admin cancelling the same order must.
    """
    return _ACTOR.get() or StatusActor(source=StatusSourceEnum.SYSTEM.value)


class OrderStatusEvent(Base, UUIDMixin):
    """One order arriving at one status."""

    __tablename__ = "order_status_events"
    __table_args__ = (
        # The only query this table serves: one order's history, in order.
        Index("ix_order_status_events_order_at", "order_id", "at"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: The status moved *to*, as its plain value. Not the `Enum` type the orders
    #: column uses: this table has to be able to record a status that was later
    #: removed from the enum, and a history that stops parsing when the
    #: vocabulary moves on is not a history.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    #: What it moved from. Null on the first row, and on a backfilled row where
    #: the evidence only named a destination.
    previous_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=StatusSourceEnum.SYSTEM.value,
        server_default=StatusSourceEnum.SYSTEM.value,
    )
    #: `SET NULL` rather than cascade: a staff member leaving must not delete
    #: the record of what they did. `actor_label` is what survives them.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    order: Mapped[Order] = relationship("Order", back_populates="status_events")
    actor: Mapped[User | None] = relationship("User")

    def __repr__(self) -> str:
        return f"<OrderStatusEvent {self.status} via {self.source} at {self.at}>"


# ── capture ───────────────────────────────────────────────────────────────────


def _value_of(status: Any) -> str | None:
    """
    The stored string, whether the caller used the enum or a bare word.

    Three call sites in `pos_order_service` used to assign `"confirmed"` and
    `"cancelled"` as literals. They now go through `order_lifecycle.transition`
    and pass enum members, but this stays literal-tolerant: the column accepts
    a string, so a future writer may hand one over again.

    The two tests are narrow on purpose. When there is no previous value to
    report — a brand-new order, or a column the session never loaded —
    SQLAlchemy hands back a member of its own `LoaderCallableStatus` enum, and a
    generic `isinstance(status, Enum)` check accepts that happily and writes its
    integer value into the column. It did: the first row of every order's
    history came out with `previous_status = '4'`, which is `NO_VALUE.value`.
    Matching `OrderStatusEnum` and `str` specifically means anything else — any
    sentinel, present or future — falls through to `None`, which is what "there
    was nothing here before" should look like.
    """
    if isinstance(status, OrderStatusEnum):
        return str(status.value)
    if isinstance(status, str):
        return status
    return None


@event.listens_for(Order.status, "set", active_history=True)
def _remember_status_change(
    target: Order, value: Any, oldvalue: Any, _initiator
) -> None:
    """
    Note a transition the moment the attribute is assigned.

    `active_history=True` is load-bearing: without it SQLAlchemy does not
    guarantee the old value is loaded, so `previous_status` would be a sentinel
    on any order whose column the session had not already read.

    Nothing is written here. This runs outside a flush, and adding to a session
    mid-assignment raises "Session is already flushing". The transition is
    parked on the instance and `_write_parked` turns it into a row.
    """
    new = _value_of(value)
    old = _value_of(oldvalue)
    if new is None or new == old:
        return

    actor = _ACTOR.get() or StatusActor(source=StatusSourceEnum.SYSTEM.value)
    inspect(target).info.setdefault(_PARKED, []).append(
        (new, old, actor, actor.at or utcnow())
    )


def pending_events(order: Order) -> list[tuple[str, str | None, StatusActor, datetime]]:
    """
    Transitions noted on this order and not yet written, as
    `(status, previous_status, actor, at)`.

    The seam between the listener and the flush, exposed because that is where
    the whole suite has to look: these tests mock the session, so nothing ever
    flushes and the rows this module exists to write are never built. Without
    this, "the courier's own moment reaches the history" could only be asserted
    against a real database — and the one guarantee most worth pinning here is
    that a Lalamove `updatedAt` never gets read in place of the epoch, which is
    a bug that already shipped once and put every delivery four hours late.
    """
    return list(inspect(order).info.get(_PARKED, ()))


@event.listens_for(Session, "before_flush")
def _write_parked(session: Session, _flush_context, _instances) -> None:
    """
    Turn parked transitions into rows, now that there is a flush to ride on.

    Scans only what this flush is about to touch. An order being deleted is
    skipped — the row would cascade away in the same transaction, so writing it
    only makes the flush order matter for no gain.
    """
    for instance in list(session.new) + list(session.dirty):
        if not isinstance(instance, Order):
            continue
        parked = inspect(instance).info.pop(_PARKED, None)
        if not parked:
            continue
        if instance in session.deleted:
            continue
        for new, old, actor, at in parked:
            session.add(
                OrderStatusEvent(
                    order=instance,
                    status=new,
                    previous_status=old,
                    at=at,
                    source=actor.source,
                    actor_id=actor.actor_id,
                    actor_label=actor.actor_label,
                    note=actor.note,
                )
            )
