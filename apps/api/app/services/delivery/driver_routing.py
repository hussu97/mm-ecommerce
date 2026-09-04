"""
Keeping the cached route between each inbound driver and their kitchen fresh.

**Why a sweep rather than a call per position.** noon Send pushes a rider's
position every 15-30 seconds and every one of those pushes is a chance to ask
Mapbox a question. At four calls a minute per live task, on an endpoint whose
answer changes about once a minute, most of them would be paying to be told the
same thing. Worse, the call would sit inside a webhook that has to answer 200 —
so a slow Mapbox would become a slow acknowledgement, and noon retries those.

So the position is written by whatever arrives, and the route is recomputed on
the sweep's own minute, off the freshest position on the row. One place, one
rate, both couriers, and nothing on the webhook path can be made slow by a third
party.

**It fails quietly and often, by design.** No token, Mapbox unreachable, a pin in
the sea, an order whose branch has no coordinates — every one of those leaves
the route columns alone and `driver_proximity` falls back to the straight-line
estimate that shipped before this existed. A counter reading "~4.2 km away"
instead of "6 min · 4.2 km away" has lost precision. That is the whole cost.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.branch import Branch
from app.models.order import Order
from app.models.order_delivery import OrderDelivery
from app.services.providers import mapbox_provider

logger = logging.getLogger(__name__)

__all__ = ["refresh_routes", "route_now"]


async def refresh_routes(
    db: AsyncSession,
    *,
    limit: int = 25,
    now: datetime | None = None,
) -> int:
    """
    Re-route every delivery with a driver still coming. Returns how many moved.

    Called from `delivery_scheduler.sweep_once`, after the position sweep — a route
    computed from last minute's pin would be a minute of driving out of date
    before anybody read it.

    Both couriers, one query. The two differ entirely in how a position *arrives*
    — noon Send pushes, Lalamove has to be asked — and not at all in what the
    counter wants done with it once it is on the row.
    """
    if not mapbox_provider.is_configured():
        return 0

    moment = now or datetime.now(timezone.utc)
    stale = moment - timedelta(seconds=settings.MAPBOX_MIN_ROUTE_INTERVAL_S)

    rows = (
        (
            await db.execute(
                select(OrderDelivery)
                .join(Order, Order.id == OrderDelivery.order_id)
                .options(selectinload(OrderDelivery.order).selectinload(Order.branch))
                .where(
                    OrderDelivery.driver_latitude.is_not(None),
                    OrderDelivery.driver_longitude.is_not(None),
                    OrderDelivery.driver_location_at.is_not(None),
                    Order.branch_id.is_not(None),
                    or_(
                        OrderDelivery.driver_route_at.is_(None),
                        OrderDelivery.driver_route_at < stale,
                    ),
                )
                .order_by(OrderDelivery.driver_route_at.asc().nullsfirst())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    routed = 0
    for delivery in rows:
        # The cheap test last, because it reads a relationship: most rows the
        # query returns are live, and the ones that are not are excluded here
        # rather than in SQL where the vocabulary is per-courier.
        if not delivery.is_driver_on_the_way_here:
            continue
        branch = delivery.order.branch if delivery.order else None
        if branch is None or branch.latitude is None or branch.longitude is None:
            continue
        if await _route_one(db, delivery, branch, at=moment):
            routed += 1
    return routed


async def _route_one(
    db: AsyncSession,
    delivery: OrderDelivery,
    branch: Branch,
    *,
    at: datetime,
) -> bool:
    """One delivery. Never raises; a failure leaves yesterday's answer alone."""
    leg = await mapbox_provider.route(
        from_latitude=float(delivery.driver_latitude),
        from_longitude=float(delivery.driver_longitude),
        to_latitude=float(branch.latitude),
        to_longitude=float(branch.longitude),
    )
    if leg is None:
        # Deliberately not stamping `driver_route_at` on a failure. Doing so
        # would make a broken token look like a fresh route for a minute at a
        # time and suppress the retry — the columns keep whatever they had, and
        # `driver_proximity` ages that out on its own.
        return False

    delivery.driver_route_km = Decimal(str(leg.distance_km))
    delivery.driver_route_minutes = Decimal(str(leg.minutes))
    delivery.driver_route_at = at
    return True


async def route_now(db: AsyncSession, delivery: OrderDelivery) -> bool:
    """
    Route one delivery immediately, ignoring the every-minute floor.

    Called the moment a driver is assigned or swapped, and that is the whole
    reason it bypasses the floor: the floor exists to stop us paying to
    re-answer a question whose answer has not changed, and a driver nobody has
    ever routed is a new question.

    **The register prints paper off this.** The driver slip carries how far off
    the rider was when it printed, and it prints within seconds of the
    assignment — so waiting for the sweep's next tick would put a slip in
    somebody's hand with the ETA missing, up to a minute before the number
    existed. That is a piece of paper that cannot be corrected.

    Best-effort like everything else on this path: a failure returns `False` and
    the slip prints without an ETA, which is what it would have said anyway.
    """
    if not mapbox_provider.is_configured():
        return False
    if not delivery.is_driver_on_the_way_here:
        return False

    # `db.get` rather than `delivery.order`. The relationship read used to be
    # the first half of an `or`, written to mean "use it if we have it, else
    # fetch it" — but an unloaded relationship under asyncpg does not evaluate
    # falsy, it raises MissingGreenlet, so the fallback was unreachable and the
    # expression meant to make the read safe was the thing that threw.
    #
    # It threw *conditionally*, which is why it survived review and the suite:
    # if anything earlier in the request had already pulled the order into the
    # session's identity map the attribute resolved with no IO and the same code
    # passed. Whether a rider assignment survived depended on what had run
    # before it.
    #
    # `db.get` consults that identity map first and only queries when the order
    # really is absent, so it is the cheap read the relationship was meant to be
    # and cannot depend on load state.
    order = await db.get(Order, delivery.order_id)
    if order is None or order.branch_id is None:
        return False

    branch = await db.get(Branch, order.branch_id)
    if branch is None or branch.latitude is None or branch.longitude is None:
        return False
    if delivery.driver_latitude is None or delivery.driver_longitude is None:
        return False

    return await _route_one(db, delivery, branch, at=datetime.now(timezone.utc))
