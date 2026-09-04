"""
When an order will arrive, and why.

**One resolver, three rules, tried in order, first match wins.** That ordering
is the contract. What it replaces is an if-chain over `books_itself` with a
single hardcoded hour applied to every zone alike — where the answer depended on
which branch a zone happened to fall through, and where changing a polygon's
courier could move a promise without anyone being able to say which change did
it.

    1. No zone            -> no promise. The address cannot be priced or served.
    2. Courier promises    -> the first day the kitchen can hand it over, plus
       next_day              that courier's transit days. A day, never an hour:
                             the van is somebody else's and its schedule is not
                             ours to name.
    3. Courier promises    -> now plus its minutes, or the next opening plus its
       minutes               minutes if the kitchen is shut.

Every number in rules 2–3 is a column, not a constant: the courier's
`unbatched_promise_minutes` and its `unbatched_promise_days`. Both are commercial
figures that move without the code moving — an SLA renegotiated, a route re-timed
— and both used to need a deploy. The admin's delivery Estimates screen writes
them. Every order dispatches directly now, so a same-day courier (Lalamove, noon
Send, Slider) resolves rule 3 and a next-day partner rule 2.

Every promise carries a `reason` naming the rule and the numbers that produced
it. It is a plain string for a log or an order record, not a screen, so "why did
this customer see 60 minutes" has an answer without re-deriving the code by hand.

**Store hours apply to both live rules. A branch holiday applies to both too**,
because it is not an hours question. One rule, stated once: *a promise never names
a day the branch is shut.* The day the kitchen can hand it over walks forward past
a closure, and `is_open` is false on a closed day so the minutes clock starts at
the next opening — and that opening skips closures too. The closed day lives in
`core.trading_hours` next to the hours, so a shut day cannot mean one thing to the
customer and another to the van.

The dispatcher itself is deliberately untouched. An order becomes dispatchable
when a human packs it, and nobody packs on a holiday — so there is only a promise
not to make.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import trading_hours
from app.models.branch import Branch
from app.models.courier import Courier, UnbatchedPromiseEnum
from app.services import branch_holiday_service
from app.services.delivery.delivery_zone_service import Zone

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
    opens_at: str | None
    closes_at: str | None
    #: Whole days the serving branch does not trade, as `YYYY-MM-DD`. Defaulted
    #: so a caller constructing a context by hand — a test, a script — gets the
    #: no-holidays reading without having to say so.
    closed_dates: frozenset[str] = frozenset()


async def _load(db: AsyncSession, zone: Zone | None, moment: datetime) -> _Context:
    if zone is None:
        return _Context(None, None, None, None)

    courier = (
        await db.execute(
            select(Courier).where(Courier.code == zone.fulfilment_provider)
        )
    ).scalar_one_or_none()

    branch = await _serving_branch(db, zone.branch_id)
    # From the day being quoted rather than from "today". They are the same
    # instant on every live call, and they stop being the same the moment
    # anything asks what a promise *would have been* — the window has to start
    # where the question starts, or the closures that mattered are missing.
    closed = (
        await branch_holiday_service.closed_dates_for(
            db, branch.id, today=trading_hours.local(moment).date()
        )
        if branch is not None
        else frozenset()
    )
    return _Context(
        zone,
        courier,
        branch.opening_from if branch is not None else None,
        branch.opening_to if branch is not None else None,
        closed,
    )


async def _serving_branch(
    db: AsyncSession, branch_id: uuid.UUID | None
) -> Branch | None:
    """
    The kitchen that bakes this zone's orders, or the pickup branch as a
    fallback.

    A zone without a branch predates zones knowing about branches, and every one
    of those was served by the single shop — so falling back to it reproduces
    exactly what such a zone has always done.

    Returns the branch rather than its two hour strings, because the hours and
    the closures have to come from the *same* one. Resolving them separately is
    how a fallback branch's opening time would end up paired with another
    branch's Eid.
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
    return branch


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
    #
    #    Two numbers, and they are about different things. `handover` is ours:
    #    the first day the kitchen can put the box in somebody's van, which the
    #    close time moves and a closure moves further. `days` is the courier's:
    #    how long their transit takes, which is one for "next day" and is a
    #    column because a partner covering Al Ain quotes two. Counted in
    #    calendar days from the handover, since their van does not observe our
    #    holidays — but the day it lands is walked off a closure too, because a
    #    promise that names a day the shop is dark is a promise nobody in the
    #    shop can answer the phone about.
    if kind == UnbatchedPromiseEnum.NEXT_DAY.value:
        days = _promise_days(courier)
        note = ""
        handover = here.date()
        if trading_hours.is_after_close(here, context.opens_at, context.closes_at):
            handover += timedelta(days=1)
            note = f" (placed {here:%H:%M} after {context.closes_at} close)"
        handover, shut = _first_open(handover, context.closed_dates)
        note += shut
        arrives, shut = _first_open(
            handover + timedelta(days=days), context.closed_dates
        )
        note += shut
        return DeliveryPromise(
            at=trading_hours.at_minute(arrives, 0),
            precision="day",
            reason=(
                f"courier:{code} next_day handover {handover:%Y-%m-%d} +{days}d{note}"
            ),
        )

    # 4. Ours to dispatch, so the hour is ours to name — once the kitchen is
    #    open. Before that, the clock starts at the door opening, not now.
    #
    #    A closure needs nothing of its own here: `is_open` is already false on
    #    one, and `next_opening` already steps over it.
    minutes = (courier.unbatched_promise_minutes if courier is not None else 60) or 60
    if trading_hours.is_open(
        here, context.opens_at, context.closes_at, context.closed_dates
    ):
        return DeliveryPromise(
            at=here + timedelta(minutes=minutes),
            precision="time",
            reason=f"courier:{code} +{minutes}m from now",
        )
    opening = trading_hours.next_opening(here, context.opens_at, context.closed_dates)
    shut = (
        "shut for the day"
        if trading_hours.is_closed(
            trading_hours.trading_date(here, context.opens_at, context.closes_at),
            context.closed_dates,
        )
        else f"shut at {here:%H:%M}"
    )
    return DeliveryPromise(
        at=opening + timedelta(minutes=minutes),
        precision="time",
        reason=(
            f"courier:{code} +{minutes}m from {opening:%Y-%m-%d %H:%M} opening ({shut})"
        ),
    )


def _promise_days(courier: Courier | None) -> int:
    """
    The courier's transit, in days, never less than one.

    A courier row with a zero or a negative in it would promise the same day or
    a day already gone; the column is NOT NULL with a default of 1, so this is
    the belt to that braces — and one is what "next day" has always meant.
    """
    days = courier.unbatched_promise_days if courier is not None else 1
    return max(1, days or 1)


def _first_open(day: date, closed_dates: frozenset[str]) -> tuple[date, str]:
    """
    The first day at or after `day` the branch trades, and a note if it moved.

    The note goes into `reason`, so a promise that jumped three days says which
    days it jumped rather than looking like an arithmetic bug.
    """
    moved = trading_hours.first_open_day(day, closed_dates)
    if moved == day:
        return day, ""
    return moved, f" (closed {day:%Y-%m-%d}→{moved:%Y-%m-%d})"


async def promise_for_zone(
    db: AsyncSession, zone: Zone | None, *, moment: datetime | None = None
) -> DeliveryPromise | None:
    """
    The delivery promise for a zone. `None` when there is nothing to promise.

    The one entry point. Everything that shows a customer a delivery time —
    the product card, the checkout, the order confirmation, the email — comes
    through here, so they cannot disagree.
    """
    now = moment or datetime.now(timezone.utc)
    context = await _load(db, zone, now)
    return resolve(context, now)
