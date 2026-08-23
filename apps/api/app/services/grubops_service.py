"""
Saying, in GrubOps' words, what this shop's terminal already said in ours.

`availability_service` answers "can this branch make this today". This turns one
of those answers into the payload GrubOps wants and posts it, so that an item
marked out on the counter goes dark on Talabat, Deliveroo and Noon without
anybody opening a second console.

**Two paths write, and they agree by construction.** The reconcile loop is
authoritative: it recomputes the whole picture from our own tables and pushes
the differences. The immediate push exists only so the aggregators do not wait
two minutes for something the shop already knows, and it is allowed to fail —
if it does, the next reconcile puts it right. Both stamp `grubops_sync_state`,
which is what stops them pushing the same fact twice.

**Nothing here decides what is out of stock.** That question has one answer, in
`availability_service`, and asking it a second way is how a menu ends up
disagreeing with the shop. This module only translates.

**Duration is the one real translation.** Our terminal offers "an hour", "until
close" and "until somebody says so"; GrubOps offers a date or the absence of
one. `indefinite` becomes UNTIL_FURTHER_NOTICE with no date; the two timed
options become UNTIL_SPECIFIC_DATE carrying the moment `availability_service`
already resolved, so the shop's own end-of-day rollover — not midnight — is what
GrubOps is told.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.models.grubops import (
    GrubOpsItemMap,
    GrubOpsLocationMap,
    GrubOpsSyncState,
)
from app.services.providers.grubops_provider import (
    STATUS_UNTIL_FURTHER_NOTICE,
    STATUS_UNTIL_SPECIFIC_DATE,
    GrubOpsError,
    provider,
)

logger = logging.getLogger(__name__)

#: Live fire-and-forget pushes, held so the loop cannot collect one mid-flight.
#: The same reason `indexnow_service` keeps a set; see the note there.
_pending: set[asyncio.Task] = set()

#: Their own client hardcodes this null — `unavailableReason` is a literal
#: `null` in the bundle's builder, with no way to set it from the console — so
#: a write of ours that carried text would be a write no human could have made.
#: Sent null for the same reason `GRUBOPS_SOURCE` says "grubOps 2.0": these
#: records should be indistinguishable from the ones the console writes.
_REASON = None

#: What GrubOps says when asked to put back something it never took away.
#:
#: Not a failure. The reconcile loop only sends differences, so this appears
#: when GrubOps already agrees — somebody cleared the item in their console
#: between two ticks, most often. Treating it as an error would leave the row
#: marked failing for ever over a race whose outcome was the one we wanted.
_ALREADY_AVAILABLE = "not currently marked as unavailable"

#: How much a return time has to move before it is worth resending. `end_of_day`
#: resolves to a very slightly different instant on every tick, and without a
#: tolerance the loop would push the same fact all evening.
_UNTIL_EPSILON_SECONDS = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_enabled() -> bool:
    """Whether anything here should talk to GrubOps at all."""
    return bool(settings.GRUBOPS_SYNC_ENABLED and provider.is_configured)


@dataclass(frozen=True)
class Desired:
    """What one mapped item's availability ought to be at one branch."""

    item_map_id: uuid.UUID
    #: The GrubOps identity, already resolved — see `_item_info`.
    brand_id: str
    recipe_id: str | None
    modifier_id: str | None
    child_modifier_id: str | None
    grubops_type: str
    available: bool
    #: When it comes back, or None for "until somebody says so". Only ever set
    #: while `available` is false.
    until: datetime | None


def _item_info(
    desired: Desired, *, partner_id: str, location_id: str
) -> dict[str, Any]:
    """The identity block every availability call carries.

    All three id columns are sent whether or not they are set, because the
    field names are what GrubOps matches on and an absent key is not the same
    as a null one to a Java service.
    """
    return {
        "partnerId": partner_id,
        "locationId": location_id,
        "brandId": desired.brand_id,
        "recipeId": desired.recipe_id,
        "modifierId": desired.modifier_id,
        "childModifierId": desired.child_modifier_id,
        "type": desired.grubops_type,
    }


def unavailable_body(
    desired: Desired, *, partner_id: str, location_id: str, source: str
) -> dict[str, Any]:
    """One item, going off the menu."""
    timed = desired.until is not None
    return {
        "itemInfo": _item_info(desired, partner_id=partner_id, location_id=location_id),
        "source": source,
        "status": (
            STATUS_UNTIL_SPECIFIC_DATE if timed else STATUS_UNTIL_FURTHER_NOTICE
        ),
        "unavailabilityInfo": {
            # ISO-8601 in UTC. `availability_service` has already turned "until
            # close" into the branch's own rollover, so this is a real instant
            # rather than a duration GrubOps would have to interpret.
            "unavailableTill": (
                desired.until.astimezone(timezone.utc).isoformat() if timed else None
            ),
            "unavailableReason": _REASON,
        },
    }


def available_body(
    desired: Desired, *, partner_id: str, location_id: str, source: str
) -> dict[str, Any]:
    """One item, coming back.

    The bare identity, flat — **not** the `{itemInfo, source, …}` envelope its
    sibling takes, and no `source` at all. The two endpoints genuinely differ:
    their bundle builds the unavailable call from one mapper and this one from
    another, and sending the wrapped shape here is answered with
    `{"partnerId": ["must not be null"], …}` for every field of the identity,
    because they are being looked for at the top level.

    `source` is accepted as an argument and ignored so the two builders stay
    interchangeable to their callers.
    """
    return _item_info(desired, partner_id=partner_id, location_id=location_id)


def _moved(previous: datetime | None, current: datetime | None) -> bool:
    """Whether a return time has changed by enough to be worth a call."""
    if (previous is None) != (current is None):
        return True
    if previous is None or current is None:
        return False
    return abs((current - previous).total_seconds()) > _UNTIL_EPSILON_SECONDS


def needs_push(state: GrubOpsSyncState | None, desired: Desired) -> bool:
    """Whether GrubOps has been told this already.

    A missing row means "never told", which is not the same as "told it was
    available" — the first tick after a mapping is approved has to say
    something, or an item that is already out would stay on the aggregators.
    """
    if state is None or state.last_pushed_available is None:
        return True
    if state.last_pushed_available != desired.available:
        return True
    # Only a thing that is *out* has a return time worth comparing.
    if not desired.available and _moved(state.last_pushed_until, desired.until):
        return True
    return False


async def push_deltas(
    db: AsyncSession,
    *,
    location: GrubOpsLocationMap,
    deltas: list[Desired],
) -> int:
    """
    Send one location's changes and record what was sent.

    Batched into at most two calls — everything going off, everything coming
    back — because GrubOps takes a list and one round trip per item would be
    both slower and ruder.

    Raises `GrubOpsError` on failure, deliberately: the caller decides whether
    that is worth a retry (the loop) or a shrug (the immediate push). Nothing is
    recorded as pushed unless the call that pushed it returned.
    """
    if not deltas:
        return 0

    partner_id = location.grubops_partner_id
    location_id = location.grubops_location_id
    source = settings.GRUBOPS_SOURCE

    going_out = [d for d in deltas if not d.available]
    coming_back = [d for d in deltas if d.available]

    if going_out:
        await provider.mark_unavailable(
            [
                unavailable_body(
                    d, partner_id=partner_id, location_id=location_id, source=source
                )
                for d in going_out
            ]
        )
    if coming_back:
        try:
            await provider.mark_available(
                [
                    available_body(
                        d, partner_id=partner_id, location_id=location_id, source=source
                    )
                    for d in coming_back
                ]
            )
        except GrubOpsError as exc:
            # See `_ALREADY_AVAILABLE`. The end state is the one we asked for,
            # so it is recorded as pushed rather than retried for ever.
            if _ALREADY_AVAILABLE not in str(exc):
                raise
            logger.info(
                "GrubOps: %d item(s) were already available at %s",
                len(coming_back),
                location_id,
            )

    await record_pushed(db, branch_id=location.branch_id, deltas=deltas)
    return len(deltas)


async def record_pushed(
    db: AsyncSession, *, branch_id: uuid.UUID, deltas: list[Desired]
) -> None:
    """Stamp what GrubOps has now been told, so the next tick sees no delta."""
    if not deltas:
        return

    existing = {
        row.grubops_item_map_id: row
        for row in (
            await db.execute(
                select(GrubOpsSyncState).where(
                    GrubOpsSyncState.branch_id == branch_id,
                    GrubOpsSyncState.grubops_item_map_id.in_(
                        [d.item_map_id for d in deltas]
                    ),
                )
            )
        )
        .scalars()
        .all()
    }

    now = _now()
    for delta in deltas:
        row = existing.get(delta.item_map_id)
        if row is None:
            row = GrubOpsSyncState(
                branch_id=branch_id, grubops_item_map_id=delta.item_map_id
            )
            db.add(row)
        row.last_pushed_available = delta.available
        row.last_pushed_until = None if delta.available else delta.until
        row.last_pushed_at = now
        # Cleared rather than left: the review screen shows this, and a stale
        # error beside a working item is worse than no error at all.
        row.last_error = None

    await db.flush()


async def record_failure(
    db: AsyncSession, *, branch_id: uuid.UUID, deltas: list[Desired], error: str
) -> None:
    """Note why a push did not land, without claiming it did.

    `last_pushed_*` is deliberately untouched — leaving it stale is what makes
    the next tick try again.
    """
    if not deltas:
        return
    existing = {
        row.grubops_item_map_id: row
        for row in (
            await db.execute(
                select(GrubOpsSyncState).where(
                    GrubOpsSyncState.branch_id == branch_id,
                    GrubOpsSyncState.grubops_item_map_id.in_(
                        [d.item_map_id for d in deltas]
                    ),
                )
            )
        )
        .scalars()
        .all()
    }
    for delta in deltas:
        row = existing.get(delta.item_map_id)
        if row is None:
            row = GrubOpsSyncState(
                branch_id=branch_id, grubops_item_map_id=delta.item_map_id
            )
            db.add(row)
        row.last_error = error[:500]
    await db.flush()


# ── the immediate path ────────────────────────────────────────────────────────


async def _push_many(
    *,
    branch_id: uuid.UUID,
    product_ids: list[uuid.UUID],
    option_ids: list[uuid.UUID],
    in_stock: bool,
    until: datetime | None,
) -> None:
    """The changed entities, on their own session, off the request's path."""
    async with AsyncSessionFactory() as db:
        location = (
            await db.execute(
                select(GrubOpsLocationMap).where(
                    GrubOpsLocationMap.branch_id == branch_id,
                    GrubOpsLocationMap.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if location is None:
            # A branch that does not trade on GrubOps. Not an error, and not
            # worth a log line every time somebody marks something out.
            return

        conditions = []
        if product_ids:
            conditions.append(GrubOpsItemMap.product_id.in_(product_ids))
        if option_ids:
            conditions.append(GrubOpsItemMap.modifier_option_id.in_(option_ids))
        if not conditions:
            return

        mappings = (
            (
                await db.execute(
                    select(GrubOpsItemMap).where(
                        GrubOpsItemMap.approved.is_(True), or_(*conditions)
                    )
                )
            )
            .scalars()
            .all()
        )
        if not mappings:
            # Unmapped, or mapped but not yet approved. The reconcile loop
            # counts these; one unmapped item is not worth interrupting anybody.
            return

        deltas = [
            Desired(
                item_map_id=m.id,
                brand_id=m.grubops_brand_id,
                recipe_id=m.grubops_recipe_id,
                modifier_id=m.grubops_modifier_id,
                child_modifier_id=m.grubops_child_modifier_id,
                grubops_type=m.grubops_type,
                available=in_stock,
                until=None if in_stock else until,
            )
            for m in mappings
        ]
        await push_deltas(db, location=location, deltas=deltas)
        await db.commit()


def push_change_in_background(
    *,
    branch_id: uuid.UUID,
    product_ids: list[uuid.UUID] | None = None,
    option_ids: list[uuid.UUID] | None = None,
    in_stock: bool,
    until: datetime | None = None,
) -> None:
    """
    Tell GrubOps about a change and stop caring about it.

    Called from the routes after the availability write, never from inside
    `availability_service` — the "all options" helper there loops, and hooking
    it would fire one of these per filling.

    The new state is **passed in rather than read back**. The request's
    transaction has not committed when this is scheduled, so a task that went
    looking for the rows might see either side of the commit; carrying the
    values makes the ordering irrelevant. The cost is that a request which
    rolls back after this point has told GrubOps about a change that did not
    happen — which the next reconcile tick corrects, and which is why this is
    allowed to be best-effort at all.

    Never raises and never blocks: the person who pressed the button is owed an
    answer about their own shop, not a wait on somebody else's API.
    """
    if not is_enabled():
        return
    products = list(product_ids or [])
    options = list(option_ids or [])
    if not products and not options:
        return

    async def _run() -> None:
        try:
            await _push_many(
                branch_id=branch_id,
                product_ids=products,
                option_ids=options,
                in_stock=in_stock,
                until=until,
            )
        except GrubOpsError as exc:
            logger.warning("GrubOps: immediate push failed (%s)", exc)
        except Exception as exc:  # noqa: BLE001 — a hint must not escape
            logger.warning("GrubOps: immediate push failed unexpectedly: %s", exc)

    try:
        task = asyncio.create_task(_run())
    except RuntimeError:
        # No running loop — a management command, or a test.
        return
    _pending.add(task)
    task.add_done_callback(_pending.discard)
