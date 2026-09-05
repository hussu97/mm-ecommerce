"""One-off cleanup for orders corrupted by the cross-channel-merge bug.

Before the channel-scope fix, `promote._find_convergence_order` matched a scrape
to an already-filed MM order by its short pickup code + branch + business day, but
NOT by channel. That short code is a per-branch-per-day sequence that DIFFERENT
channels reuse, so a masked Keeta backfill converged onto — and via
`_refresh_order` overwrote the money, timestamps, display code, fees and blank
customer of — a foreign-channel order (typically a Deliveroo GrubOps order that
shared the code on the same branch and day). The fix stops this happening again;
this heals the rows already damaged.

The signature of a merge is an MM order that more than one channel's
`aggregator_order` rows point at. For each such order this:

  * detaches the INTRUDER aggs (the ones whose channel is not the order's own) —
    `mm_order_id` / `promoted_at` back to NULL — so they re-promote to their OWN
    order under the now channel-scoped code;
  * clears any customer / rider value the merge wrote that is masked (`*`) or that
    matches only an intruder (i.e. is the intruder's value, not the order's own);
  * restores the order from its LEGITIMATE source —
      - GrubOps order (has a `grubops_order_map`): re-derives money, display code
        and placed-at from the stored GrubTech payload (`order_map.raw`), refills
        the customer / rider from it, then re-runs the legit-channel promotion for
        fees, fulfilment and statement-line links;
      - standalone order: re-runs the legit-channel promotion, whose
        `_refresh_order` rewrites money, timestamps, display code, fees and the
        (now blank) customer from the legitimate aggregator order.

Dry-run by default — prints the report and writes nothing. Pass --apply to write.
Run in the live API container (the DB is prod's):

  docker compose exec <api-slot> python -m scripts.heal_cross_channel_merges
  docker compose exec <api-slot> python -m scripts.heal_cross_channel_merges --apply

The live slot alternates api/api-green per deploy — derive it from `docker ps`.
Idempotent: a second run finds nothing left to heal.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from sqlalchemy import func, select

from app.models.aggregator import AggregatorOrder
from app.models.grubops_order import GrubOpsOrderMap
from app.models.order import Order
from app.services.aggregators import promote, reconcile
from app.services.grubops import grubops_orders_service as gos
from app.services.orders import order_fees


def _is_masked(value: Any) -> bool:
    """A redacted placeholder — `***`, or a JSONB address masked to `{"...": "***"}`."""
    return bool(value) and "*" in str(value)


async def _candidate_order_ids(db) -> list[Any]:
    """MM order ids that more than one channel's aggregator orders point at."""
    rows = await db.execute(
        select(AggregatorOrder.mm_order_id)
        .where(AggregatorOrder.mm_order_id.is_not(None))
        .group_by(AggregatorOrder.mm_order_id)
        .having(func.count(func.distinct(AggregatorOrder.channel)) > 1)
    )
    return [r[0] for r in rows]


def _split_aggs(
    order: Order, aggs: list[AggregatorOrder]
) -> tuple[list[AggregatorOrder], list[AggregatorOrder]]:
    """(legit, intruder): an agg is legit when the order's `aggregator_channel`
    is one of the GrubTech spellings of that agg's channel — the same set
    `_find_mm_order` scopes by. Everything else merged in by the old unscoped code."""
    legit, intruder = [], []
    for agg in aggs:
        names = reconcile.grubops_channel_names(agg.channel)
        (legit if order.aggregator_channel in names else intruder).append(agg)
    return legit, intruder


def _clear_suspect_contact(order: Order, intruders: list[AggregatorOrder]) -> None:
    """Null any customer/rider field on the order that is masked or that carries an
    intruder's value — so the legitimate source refills it. A value the order shares
    with its own legit source is left alone."""
    intruder_names = {a.customer_name for a in intruders if a.customer_name}
    intruder_phones = {a.customer_phone for a in intruders if a.customer_phone}
    intruder_driver_names = {a.driver_name for a in intruders if a.driver_name}
    intruder_driver_phones = {a.driver_phone for a in intruders if a.driver_phone}

    if _is_masked(order.customer_name) or order.customer_name in intruder_names:
        order.customer_name = None
    if _is_masked(order.customer_phone) or order.customer_phone in intruder_phones:
        order.customer_phone = None
        order.customer_phone_country = None
        order.customer_phone_type = None
        order.customer_phone_access_code = None
    if _is_masked(order.shipping_address_snapshot):
        order.shipping_address_snapshot = None
    if (
        _is_masked(order.aggregator_driver_name)
        or order.aggregator_driver_name in intruder_driver_names
    ):
        order.aggregator_driver_name = None
    if (
        _is_masked(order.aggregator_driver_phone)
        or order.aggregator_driver_phone in intruder_driver_phones
    ):
        order.aggregator_driver_phone = None


async def _restore_from_grubops(db, order: Order, gmap: GrubOpsOrderMap) -> None:
    """Rebuild the money, display code, placed-at and contact of a GrubOps order
    from its stored GrubTech payload — the fields the merge's `_refresh_order`
    overwrote — then leave fees/fulfilment to the legit-channel promotion."""
    raw = gmap.raw or {}
    header = raw.get("orderHeader") or {}

    for field, value in gos.money_fields_from_info(raw).items():
        setattr(order, field, value)

    order.aggregator_display_code = gos._driver_code(header, gmap.external_id, raw)
    placed = gos._placed_at(raw)
    if placed is not None:
        order.created_at = placed

    name, phone, pcountry, ptype, pcode, email = gos._customer_fields(
        raw.get("customer") or {}
    )
    if not order.customer_name and name:
        order.customer_name = name
    if not order.customer_phone and phone:
        order.customer_phone = phone
        order.customer_phone_country = pcountry
        order.customer_phone_type = ptype
        order.customer_phone_access_code = pcode
    if email and not order.email:
        order.email = email

    # Rider straight from the payload (fills or updates; never wipes a real value).
    gos._apply_driver_info(order, raw)


async def _heal_order(db, order_id: Any, *, apply: bool) -> dict[str, Any]:
    order = await db.scalar(select(Order).where(Order.id == order_id))
    aggs = list(
        await db.scalars(
            select(AggregatorOrder).where(AggregatorOrder.mm_order_id == order_id)
        )
    )
    gmap = await db.scalar(
        select(GrubOpsOrderMap).where(GrubOpsOrderMap.mm_order_id == order_id)
    )
    legit, intruder = _split_aggs(order, aggs)

    report = {
        "order_number": order.order_number,
        "aggregator_channel": order.aggregator_channel,
        "is_grubops": gmap is not None,
        "legit_channels": sorted({a.channel for a in legit}),
        "intruder_channels": sorted({a.channel for a in intruder}),
        "customer_name": order.customer_name,
        "customer_masked": _is_masked(order.customer_name),
        "total": str(order.total),
    }

    if not intruder:
        report["action"] = "skipped: no intruder aggs (already clean)"
        return report
    if not legit:
        # No agg matches the order's own channel — cannot tell which source is
        # authoritative, so do not guess. Flag for manual attention.
        report["action"] = "SKIPPED — no legit agg matches order.aggregator_channel"
        return report
    if gmap is not None and not (gmap.raw or {}).get("orderHeader"):
        report["action"] = "SKIPPED — GrubOps order but order_map.raw has no payload"
        return report

    if not apply:
        report["action"] = "would heal"
        return report

    # 1. Detach the intruders so they re-promote to their own order.
    for agg in intruder:
        agg.mm_order_id = None
        agg.promoted_at = None

    # 2. Clear the contact the merge wrote.
    _clear_suspect_contact(order, intruder)

    # 3. Restore from the legitimate source.
    if gmap is not None:
        await _restore_from_grubops(db, order, gmap)
    await db.flush()
    for agg in legit:
        # Re-run the real promotion: GrubOps path re-stamps fees, fulfilment and
        # statement links; standalone path's _refresh_order rewrites money,
        # timestamps, display code and fills the (now blank) customer.
        await promote.promote_order(db, agg, draw_stock=False)
    # GrubOps promotion only overlays fees when the agg already carries a settled
    # figure; re-stamp from the legit agg so a keeta commission left on the order is
    # replaced with the modelled/settled estimate for the real channel.
    if gmap is not None and legit:
        await order_fees.stamp(db, order, **promote._actual_fee_overrides(legit[0]))
    await db.flush()

    report["action"] = "healed"
    report["customer_name_after"] = order.customer_name
    report["total_after"] = str(order.total)
    return report


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the fixes (default: dry-run report only)",
    )
    args = parser.parse_args()

    from app.core.database import AsyncSessionFactory

    healed = 0
    async with AsyncSessionFactory() as db:
        order_ids = await _candidate_order_ids(db)
        print(
            f"{'APPLY' if args.apply else 'DRY-RUN'}: "
            f"{len(order_ids)} MM order(s) with multi-channel aggregator links\n"
        )
        for order_id in order_ids:
            report = await _heal_order(db, order_id, apply=args.apply)
            print(
                f"  #{report['order_number']} [{report['aggregator_channel']}]"
                f" grubops={report['is_grubops']}"
                f" intruders={report['intruder_channels']}"
                f" customer={report['customer_name']!r}"
                f" -> {report['action']}"
            )
            if report.get("action") in ("healed",):
                healed += 1
                print(
                    f"      customer_after={report.get('customer_name_after')!r}"
                    f" total {report['total']} -> {report.get('total_after')}"
                )
        if args.apply:
            await db.commit()
            print(f"\nCommitted. Healed {healed} order(s).")
        else:
            print("\nDry run — nothing written. Re-run with --apply to heal.")


if __name__ == "__main__":
    asyncio.run(main())
