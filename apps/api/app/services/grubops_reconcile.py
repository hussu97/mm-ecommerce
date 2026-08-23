"""
Keeping GrubOps' idea of the menu the same as this shop's, minute by minute.

The terminal's out-of-stock **expires on read**. `out_of_stock_until` is
compared with now every time the question is asked, so an item marked out until
close is on sale again the moment the clock passes — and nothing fires when that
happens. No job runs, no row changes, no event is emitted. That design is right
for us and it is exactly why a push-on-change integration would be wrong: the
"back in stock" half of every timed window would simply never be sent, and a
week later the aggregators would be showing half a menu that the shop has been
selling all along.

So the authority here is a loop that **recomputes** rather than listens. Every
tick it asks `availability_service` what each mapped branch can actually make
right now — the same question, through the same code, that the storefront and
the cart ask — and sends GrubOps the differences from what it was last told.
An hour lapsing is not an event to be caught; it is just a different answer to
the same question, and the next tick sees it.

That also makes the whole thing self-healing. A dropped immediate push, a deploy
in the middle of a window, somebody editing availability in the GrubOps console
by hand: all of them are drift, and drift is what a diff is for.

The shape — `run_forever` + `sweep_once`, an advisory lock so a second worker
achieves nothing, a loop in the app's own lifespan — is `batch_scheduler`'s,
because this stack has no cron and nothing that survives the container.

It ticks every two minutes rather than every minute. Availability is changed by
a person walking to a screen, and a faster tick would only spend somebody else's
rate limit to be ninety seconds earlier.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import advisory_lock
from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.models.branch import Branch
from app.models.grubops import GrubOpsItemMap, GrubOpsLocationMap, GrubOpsSyncState
from app.models.menu import BranchModifierOption, BranchProduct
from app.services import availability_service, grubops_service
from app.services.grubops_service import Desired
from app.services.providers.grubops_provider import GrubOpsError

logger = logging.getLogger(__name__)

#: "mmBATCH" + 3. The flat 64-bit namespace `batch_scheduler` (…4801) and
#: `log_retention` (…4802) live in; a new loop needs a number nobody else has,
#: or two unrelated sweeps take turns doing nothing.
_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_4803


def _tick_seconds() -> int:
    return settings.GRUBOPS_RECONCILE_TICK_SECONDS


async def _return_times(
    db: AsyncSession, branch_id: uuid.UUID
) -> tuple[dict[uuid.UUID, datetime | None], dict[uuid.UUID, datetime | None]]:
    """
    When each currently-out thing is due back, per product and per option.

    Read separately from the "what is out" sets because those apply expiry and
    return only ids — and GrubOps wants the actual moment so it can show a
    countdown rather than an indefinite blackout.
    """
    products = {
        row.product_id: row.out_of_stock_until
        for row in (
            await db.execute(
                select(BranchProduct).where(BranchProduct.branch_id == branch_id)
            )
        )
        .scalars()
        .all()
    }
    options = {
        row.modifier_option_id: row.out_of_stock_until
        for row in (
            await db.execute(
                select(BranchModifierOption).where(
                    BranchModifierOption.branch_id == branch_id
                )
            )
        )
        .scalars()
        .all()
    }
    return products, options


async def desired_state(db: AsyncSession, branch_id: uuid.UUID) -> list[Desired]:
    """
    What GrubOps ought to believe about every approved-mapped item at a branch.

    The two "what is out" queries are `availability_service`'s own, so expiry is
    applied by the database at `now()` and a lapsed window reads as available
    here without anything having had to notice it lapse.
    """
    out_products = set(
        (await db.execute(availability_service.unavailable_product_ids_at(branch_id)))
        .scalars()
        .all()
    )
    out_options = set(
        (await db.execute(availability_service.unavailable_option_ids_at(branch_id)))
        .scalars()
        .all()
    )
    until_products, until_options = await _return_times(db, branch_id)

    mappings = (
        (
            await db.execute(
                select(GrubOpsItemMap).where(GrubOpsItemMap.approved.is_(True))
            )
        )
        .scalars()
        .all()
    )

    desired: list[Desired] = []
    for mapping in mappings:
        if mapping.product_id is not None:
            available = mapping.product_id not in out_products
            until = None if available else until_products.get(mapping.product_id)
        else:
            available = mapping.modifier_option_id not in out_options
            until = None if available else until_options.get(mapping.modifier_option_id)
        desired.append(
            Desired(
                item_map_id=mapping.id,
                brand_id=mapping.grubops_brand_id,
                recipe_id=mapping.grubops_recipe_id,
                modifier_id=mapping.grubops_modifier_id,
                child_modifier_id=mapping.grubops_child_modifier_id,
                grubops_type=mapping.grubops_type,
                available=available,
                until=until,
            )
        )
    return desired


async def _reconcile_branch(db: AsyncSession, location: GrubOpsLocationMap) -> int:
    """One branch: compute, diff, push. Returns how many changes went."""
    desired = await desired_state(db, location.branch_id)
    if not desired:
        return 0

    states = {
        row.grubops_item_map_id: row
        for row in (
            await db.execute(
                select(GrubOpsSyncState).where(
                    GrubOpsSyncState.branch_id == location.branch_id
                )
            )
        )
        .scalars()
        .all()
    }

    deltas = [
        d for d in desired if grubops_service.needs_push(states.get(d.item_map_id), d)
    ]
    if not deltas:
        return 0

    try:
        return await grubops_service.push_deltas(db, location=location, deltas=deltas)
    except GrubOpsError as exc:
        # Recorded, not raised: the next tick recomputes and tries again, and
        # `last_pushed_*` is deliberately left stale so it will.
        logger.warning(
            "GrubOps: %s changes failed for branch %s (%s)",
            len(deltas),
            location.branch_id,
            exc,
        )
        await grubops_service.record_failure(
            db, branch_id=location.branch_id, deltas=deltas, error=str(exc)
        )
        return 0


async def sweep_once() -> dict[str, int]:
    """
    One pass over every branch that trades on GrubOps.

    Returns changes pushed per branch name — `{}` when another worker held the
    lock, which is ordinary rather than a failure.
    """
    if not grubops_service.is_enabled():
        return {}

    async with advisory_lock.held(_ADVISORY_LOCK_KEY, name="grubops reconcile") as mine:
        if not mine:
            return {}

        async with AsyncSessionFactory() as db:
            rows = (
                await db.execute(
                    select(GrubOpsLocationMap, Branch)
                    .join(Branch, Branch.id == GrubOpsLocationMap.branch_id)
                    .where(GrubOpsLocationMap.is_active.is_(True))
                )
            ).all()

            pushed: dict[str, int] = {}
            for location, branch in rows:
                # Per branch, so Sharjah's outage does not cost Barsha Heights
                # its sync — the sub-sweep posture `batch_scheduler` uses.
                try:
                    count = await _reconcile_branch(db, location)
                except Exception:  # noqa: BLE001 — one branch must not end the pass
                    logger.exception(
                        "GrubOps: reconcile failed for branch %s", branch.name
                    )
                    continue
                if count:
                    pushed[branch.name] = count

            await db.commit()
            return pushed


async def run_forever() -> None:
    """
    The loop. Cancelled on shutdown; never allowed to die on an exception.

    A tick that raises must be survivable. The failure mode of not reconciling
    is an aggregator selling something the kitchen cannot make, which is worth
    retrying in two minutes rather than giving up on until the next deploy.
    """
    logger.info("GrubOps reconcile started (every %ss)", _tick_seconds())
    while True:
        try:
            # Sleeps first: boot is the busiest moment a process has, and
            # nothing here is urgent enough to compete with serving requests.
            await asyncio.sleep(_tick_seconds())
            pushed = await sweep_once()
            if pushed:
                logger.info(
                    "GrubOps reconcile pushed %s",
                    ", ".join(f"{n} at {branch}" for branch, n in pushed.items()),
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — one bad tick must not stop them all
            logger.exception("GrubOps reconcile tick failed")
