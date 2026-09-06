"""Heal MM orders the marketplace settled as CANCELLED but we recorded as delivered.

Until 2026-09-06 three separate paths asserted a doorstep nobody had reported: the
GrubOps `OrderCompleted` mapping, the `packed → delivered` auto-close, and promote's
`"picked up"`. 1,141 aggregator orders went `packed → delivered` inside sixty
seconds. Almost all of those really were delivered — but when one was NOT (Talabat
3872488968: cancelled because no driver ever collected it) the correction could
never land, because `delivered → cancelled` is refused and promote skips a refused
move quietly. The order sat `delivered` for good.

`55b9d511` stops the invention, so this cannot recur: an aggregator order now rests
at `out_for_delivery` and a marketplace cancellation is allowed to leave that.
This heals the rows that were already stuck.

**What it changes.** For every MM order whose own `aggregator_order` settled as
cancelled while MM says delivered, one `order_lifecycle.transition` to `cancelled`,
attributed to the aggregator and stamped with the channel's cancellation time.
`DELIVERED` is named in `extra_from` HERE and only here — deliberately not in the
promote path, where a delivered order must stay delivered — because this is a human
running a correction over known-bad history, not a webhook rewriting a settled
state on its own.

**What that makes happen**, via the lifecycle's own consequences: the stock goes
back on the shelf and the register check is voided. No money moves — an aggregator
order has no MM card to refund and no MM delivery row to cancel
(`order_lifecycle._is_mm_fulfilled`), so the marketplace's own refund to the
customer is untouched and unduplicated.

Dry-run by default — prints the report and writes nothing. Pass --apply to write.
Run in the live API container (the DB is prod's); the live slot alternates
api/api-green per deploy, so derive it from `docker ps`:

  docker compose exec <api-slot> python -m scripts.heal_aggregator_cancellations
  docker compose exec <api-slot> python -m scripts.heal_aggregator_cancellations --apply

Idempotent: a second run finds nothing left to heal.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionFactory
from app.models.aggregator import AggregatorOrder
from app.models.order import Order, OrderStatusEnum
from app.models.order_status_event import StatusSourceEnum, acting_as
from app.models.pos_order import PosOrderStatusEnum
from app.services.aggregators.promote import _target_status
from app.services.orders import order_lifecycle


async def _stuck(db) -> list[tuple[Order, AggregatorOrder]]:
    """Orders MM calls delivered whose own marketplace row settled as cancelled.

    Joined on `mm_order_id` rather than matched on a code: this must only ever
    touch an order the channel itself claims, never a look-alike.
    """
    rows = (
        await db.execute(
            select(Order, AggregatorOrder)
            .join(AggregatorOrder, AggregatorOrder.mm_order_id == Order.id)
            .where(Order.status == OrderStatusEnum.DELIVERED)
            .options(selectinload(Order.items))
        )
    ).all()
    return [
        (order, agg)
        for order, agg in rows
        if _target_status(agg.channel, agg.status) == OrderStatusEnum.CANCELLED
    ]


async def _checks_still_counting(db) -> list[tuple[Order, AggregatorOrder]]:
    """Cancelled aggregator orders whose register check the auto-close left `closed`.

    A closed check counts as a completed sale in `pos_reports._COMPLETED_SALE`, so a
    cancelled order sitting `closed` is money on a report that was never earned.
    """
    rows = (
        await db.execute(
            select(Order, AggregatorOrder)
            .join(AggregatorOrder, AggregatorOrder.mm_order_id == Order.id)
            .where(
                Order.status == OrderStatusEnum.CANCELLED,
                Order.pos_status == PosOrderStatusEnum.CLOSED.value,
            )
        )
    ).all()
    return [
        (order, agg)
        for order, agg in rows
        if _target_status(agg.channel, agg.status) == OrderStatusEnum.CANCELLED
    ]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write the corrections (default: dry run)"
    )
    args = parser.parse_args()

    async with AsyncSessionFactory() as db:
        stuck = await _stuck(db)
        for order, agg in stuck:
            print(
                f"  status  {order.order_number}  {agg.channel:<10}"
                f" {agg.external_order_id:<18} channel={agg.status!r}"
                f"  mm={order.status.value}"
            )
        # Read before the status pass runs, so a dry run reports only what is stuck
        # today; the orders healed below are re-read afterwards in the same run.
        for order, agg in await _checks_still_counting(db):
            print(
                f"  check   {order.order_number}  {agg.channel:<10}"
                f" {agg.external_order_id:<18} cancelled but pos_status=closed"
            )
        if not stuck and not await _checks_still_counting(db):
            print("Nothing to heal.")
            return
        if not args.apply:
            print("\nDry run — nothing written. Re-run with --apply to correct these.")
            return

        healed = 0
        for order, agg in stuck:
            with acting_as(
                StatusSourceEnum.AGGREGATOR,
                at=agg.cancelled_at or agg.placed_at,
                note=(
                    f"healed: {agg.channel} settled this order as {agg.status!r}; "
                    "MM had it delivered from the pre-2026-09-06 auto-close"
                ),
            ):
                moved = await order_lifecycle.transition(
                    db,
                    order,
                    OrderStatusEnum.CANCELLED,
                    extra_from=(OrderStatusEnum.DELIVERED,),
                )
            if moved:
                healed += 1
                print(f"  healed status {order.order_number}")

        voided = 0
        for order, _agg in await _checks_still_counting(db):
            order.pos_status = PosOrderStatusEnum.VOID.value
            voided += 1
            print(f"  voided check  {order.order_number}")
        await db.commit()
        print(
            f"\nCorrected {healed} status(es) and {voided} register check(s). "
            "Stock restored, no money moved."
        )


if __name__ == "__main__":
    asyncio.run(main())
