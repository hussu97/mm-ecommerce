"""
When an order will arrive, and why.

**One resolver, four rules, tried in order, first match wins.** That ordering is
the contract. What it replaces is an if-chain over `books_itself` / `is_batched`
with a single hardcoded hour applied to every zone alike — where the answer
depended on which branch a zone happened to fall through, and where changing a
polygon's courier or a batch's schedule could move a promise without anyone
being able to say which change did it.

    1. No zone            -> no promise. The address cannot be priced or served.
    2. Zone in a batch     -> that group's next window close, plus the group's
       group                 own minutes-to-door.
    3. Courier promises    -> the next trading day. A day, never an hour: the
       next_day              van is somebody else's and its schedule is not ours
                             to name.
    4. Courier promises    -> now plus its minutes, or the next opening plus its
       minutes               minutes if the kitchen is shut.

Every promise carries a `reason` naming the rule and the numbers that produced
it. It is a plain string for a log or an order record, not a screen. Its job is
that when somebody moves a polygon between groups or edits a window, the promise
says what it was computed from, so "why did this customer see 90 minutes" has an
answer that does not require re-deriving the code by hand.

**Store hours apply to rules 3 and 4 only.** A batch window already encodes when
the kitchen can pack — the 23:00–12:00 slot exists precisely because nothing
leaves overnight — so applying trading hours on top of it would subtract the
same closure twice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import trading_hours
from app.models.branch import Branch
from app.models.courier import Courier, UnbatchedPromiseEnum
from app.models.delivery_batch import DeliveryBatchGroup, DeliveryBatchWindow
from app.services import batching_service
from app.services.delivery_zone_service import Zone

__all__ = [
    "DeliveryPromise",
    "promise_for_zone",
    "resolve",
]


@dataclass(frozen=True)
class DeliveryPromise:
    """When the box should be through the door, how precisely, and on what basis."""

    #: On the shop's clock, which is the clock the customer is standing on.
    at: datetime
    #: `"time"` when an hour can be named, `"day"` when only the date is ours to
    #: promise. Collapsing the two would invent a precision belonging to
    #: somebody else's schedule.
    precision: str
    #: Which rule fired, and the numbers it used. For logs and the order record.
    #: Never shown to a customer — it names couriers, and the storefront is
    #: careful not to.
    reason: str


# ── the pieces each rule needs ───────────────────────────────────────────────


@dataclass(frozen=True)
class _Context:
    """Everything the rules read, fetched once."""

    zone: Zone | None
    courier: Courier | None
    group: DeliveryBatchGroup | None
    windows: list[DeliveryBatchWindow]
    opens_at: str | None
    closes_at: str | None


async def _load(db: AsyncSession, zone: Zone | None) -> _Context:
    if zone is None:
        return _Context(None, None, None, [], None, None)

    courier = (
        await db.execute(
            select(Courier).where(Courier.code == zone.fulfilment_provider)
        )
    ).scalar_one_or_none()

    group: DeliveryBatchGroup | None = None
    windows: list[DeliveryBatchWindow] = []
    if zone.batch_group_id is not None:
        group = (
            await db.execute(
                select(DeliveryBatchGroup).where(
                    DeliveryBatchGroup.id == zone.batch_group_id,
                    DeliveryBatchGroup.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if group is not None:
            windows = list(
                (
                    await db.execute(
                        select(DeliveryBatchWindow)
                        .where(
                            DeliveryBatchWindow.group_id == group.id,
                            DeliveryBatchWindow.is_active.is_(True),
                        )
                        .order_by(
                            DeliveryBatchWindow.start_hour,
                            DeliveryBatchWindow.start_minute,
                        )
                    )
                )
                .scalars()
                .all()
            )

    opens_at, closes_at = await _trading_hours(db, zone.branch_id)
    return _Context(zone, courier, group, windows, opens_at, closes_at)


async def _trading_hours(
    db: AsyncSession, branch_id: uuid.UUID | None
) -> tuple[str | None, str | None]:
    """
    The kitchen's hours for this zone, or the pickup branch's as a fallback.

    A zone without a branch predates zones knowing about branches, and every one
    of those was served by the single shop — so falling back to it reproduces
    exactly what such a zone has always done.
    """
    branch: Branch | None = None
    if branch_id is not None:
        branch = await db.get(Branch, branch_id)
    if branch is None:
        branch = (
            await db.execute(
                select(Branch)
                .where(
                    Branch.is_active.is_(True), Branch.receives_online_orders.is_(True)
                )
                .order_by(Branch.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()
    if branch is None:
        return None, None
    return branch.opening_from, branch.opening_to


def _next_run(
    windows: list[DeliveryBatchWindow], moment: datetime
) -> tuple[DeliveryBatchWindow, datetime] | None:
    """
    The run this order will actually join, using the dispatcher's own matcher.

    `batching_service.find_window` rather than "the earliest close from here",
    and the difference is not cosmetic. Windows are half-open, so 18:00 belongs
    to the slot *starting* at 18:00, not to the one closing on it — an order
    landing exactly on a boundary must not be promised a van that is pulling
    away as it arrives.

    It also returns None in a gap between slots, and that is the answer worth
    having: `assign_or_dispatch` dispatches such an order immediately rather
    than making it wait, so falling through to the courier's own promise is what
    makes this agree with what the dispatcher does. Two matchers would be two
    answers, and the one the customer sees would be the one that is wrong.
    """
    match = batching_service.find_window(windows, moment)
    if match is None:
        return None
    return match.window, match.dispatch_at


# ── the resolver ─────────────────────────────────────────────────────────────


def resolve(context: _Context, moment: datetime) -> DeliveryPromise | None:
    """
    The four rules, in order. Pure — every input is already on `context`, so
    this is the whole of the decision and it can be read in one sitting.
    """
    here = trading_hours.local(moment)

    # 1. Nowhere we can deliver.
    if context.zone is None:
        return None

    # 2. A declared batch group. Its windows already account for the kitchen
    #    being shut, so trading hours are deliberately not applied again.
    if context.group is not None and context.windows:
        found = _next_run(context.windows, here)
        if found is not None:
            window, closes = found
            minutes = context.group.delivery_minutes_after_dispatch
            return DeliveryPromise(
                at=closes + timedelta(minutes=minutes),
                precision="time",
                reason=(
                    f"batch:{context.group.name}/{window.label} "
                    f"closes {closes:%Y-%m-%d %H:%M} +{minutes}m"
                ),
            )

    courier = context.courier
    # A courier with no row is one nobody has configured. Treat it the way an
    # unconfigured courier has always been treated — as somebody else's van.
    kind = (
        courier.unbatched_promise_kind
        if courier is not None
        else UnbatchedPromiseEnum.NEXT_DAY.value
    )
    code = context.zone.fulfilment_provider

    # 3. Somebody else's schedule. A day, and one more day if today's trading is
    #    already over — an order at 23:30 against a 23:00 close cannot even be
    #    baked today, so promising tomorrow would be promising a day early.
    if kind == UnbatchedPromiseEnum.NEXT_DAY.value:
        days = 1
        note = ""
        if trading_hours.is_after_close(here, context.opens_at, context.closes_at):
            days = 2
            note = f" (+1, placed {here:%H:%M} after {context.closes_at} close)"
        return DeliveryPromise(
            at=trading_hours.at_minute(here.date() + timedelta(days=days), 0),
            precision="day",
            reason=f"courier:{code} next_day{note}",
        )

    # 4. Ours to dispatch, so the hour is ours to name — once the kitchen is
    #    open. Before that, the clock starts at the door opening, not now.
    minutes = (courier.unbatched_promise_minutes if courier is not None else 60) or 60
    if trading_hours.is_open(here, context.opens_at, context.closes_at):
        return DeliveryPromise(
            at=here + timedelta(minutes=minutes),
            precision="time",
            reason=f"courier:{code} +{minutes}m from now",
        )
    opening = trading_hours.next_opening(here, context.opens_at)
    return DeliveryPromise(
        at=opening + timedelta(minutes=minutes),
        precision="time",
        reason=(
            f"courier:{code} +{minutes}m from {opening:%Y-%m-%d %H:%M} opening "
            f"(shut at {here:%H:%M})"
        ),
    )


async def promise_for_zone(
    db: AsyncSession, zone: Zone | None, *, moment: datetime | None = None
) -> DeliveryPromise | None:
    """
    The delivery promise for a zone. `None` when there is nothing to promise.

    The one entry point. Everything that shows a customer a delivery time —
    the product card, the checkout, the order confirmation, the email — comes
    through here, so they cannot disagree.
    """
    context = await _load(db, zone)
    return resolve(context, moment or datetime.now(timezone.utc))
