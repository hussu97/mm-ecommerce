"""The maker-checker: where a marketplace's ledger and MM's order disagree.

Two layers, because not every branch has an MM order to check against (see the
plan). **Layer B** is the cross-system check — an aggregator order joined to the
MM order it became, comparing items, refunds and the real commission against
MM's modelled `aggregator_fee`. It runs only for GrubOps branches (Sharjah,
Barsha), since only those produce an `orders` row; an aggregator-only branch
(DSO, Karama) has nothing to check and is recorded `no_maker_side` — a fact, not
a discrepancy. **Layer A** (intra-aggregator sales↔statement↔payout) is the
coverage a future pass adds for every branch; today the row still carries what
the aggregator side knows.

The join is `(channel, external_order_id)` → `grubops_order_map (source_channel,
external_id)` → `mm_order_id` → `orders`, never `external_id` alone (numbers are
reused across channels). Output is one `aggregator_reconciliation` row per order,
upserted idempotently.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.aggregator import (
    CHANNEL_CAREEM,
    CHANNEL_DELIVEROO,
    CHANNEL_KEETA,
    CHANNEL_NOON,
    CHANNEL_TALABAT,
    GRAIN_LINE,
    MATCH_MATCHED,
    MATCH_NO_MAKER_SIDE,
    MATCH_UNMATCHED_AGG,
    AggregatorOrder,
    AggregatorOrderItem,
    AggregatorReconciliation,
)
from app.models.base import utcnow
from app.models.grubops import GrubOpsLocationMap
from app.models.grubops_order import GrubOpsOrderMap
from app.models.order import Order, OrderItem

logger = logging.getLogger(__name__)

#: How each channel's `source_channel` is spelled on `grubops_order_map` — the
#: clean `sourceDisplayName` GrubOps hands back, which the order ingest stores.
# The customer-facing marketplace name stamped on the promoted MM order's
# `aggregator_channel`. These MUST match the label GrubOps writes for the same
# marketplace (GrubOps takes it from the order payload's `foodAggregatorName`),
# so a promoted order and a GrubOps order are display-identical everywhere — the
# admin order list, dashboards and the daily report all group on this string.
# Noon's marketplace name in the payload is "Noon Food" (and the daily report's
# column code is `noon_food`), NOT "Noon" — a mismatch here would show DSO/Karama
# noon sales under a separate "Noon" bucket from Barsha/Sharjah's "Noon Food".
CHANNEL_GRUBOPS_LABEL = {
    CHANNEL_CAREEM: "Careem",
    CHANNEL_DELIVEROO: "Deliveroo",
    CHANNEL_TALABAT: "Talabat",
    CHANNEL_NOON: "Noon Food",
    CHANNEL_KEETA: "Keeta 2.0",
}

_TOL = Decimal("0.01")


def _d(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


async def _branch_has_grubops(db: AsyncSession, branch_id) -> bool:
    if branch_id is None:
        return False
    return bool(
        await db.scalar(
            select(GrubOpsLocationMap.id).where(
                GrubOpsLocationMap.branch_id == branch_id,
                GrubOpsLocationMap.is_active.is_(True),
            )
        )
    )


#: GrubTech's own `source.channel` string for a channel — what actually lands in
#: `grubops_order_map.source_channel` — which is NOT always the display label
#: `CHANNEL_GRUBOPS_LABEL` uses. GrubTech calls Noon just "Noon", while MM groups
#: its sales under "Noon Food". `_find_mm_order` matched only the display label, so
#: for Noon it found NOTHING: every Barsha/Sharjah Noon order failed to link to its
#: GrubOps order, deferred the full adopt-grace, then filed a duplicate standalone —
#: the noon-duplicate problem. Match BOTH names so the lookup finds the map row
#: whichever string GrubTech wrote. Channels whose GrubTech name equals their
#: display label need no entry here.
_GRUBOPS_SOURCE_ALIASES: dict[str, tuple[str, ...]] = {
    CHANNEL_NOON: ("Noon",),
}


async def _find_mm_order(
    db: AsyncSession,
    channel: str,
    external_order_id: str,
    display_ref: str | None = None,
):
    """The MM order this aggregator order became, matched on channel + number.

    GrubTech records the marketplace's SHORT customer code as its `externalId`
    (Noon's "2253"), while the scrape keys on the long `orderNr`. So the map is
    matched on either id the two sides might carry — the long `external_order_id`
    or the short `display_ref` — under any source_channel string GrubTech uses for
    this channel (its display label plus any `_GRUBOPS_SOURCE_ALIASES`), which keeps
    the lookup channel-scoped without missing Noon's "Noon" vs "Noon Food" split."""
    labels = [
        label
        for label in (
            CHANNEL_GRUBOPS_LABEL.get(channel),
            *_GRUBOPS_SOURCE_ALIASES.get(channel, ()),
        )
        if label
    ]
    refs = [r for r in (external_order_id, display_ref) if r]
    map_row = await db.scalar(
        select(GrubOpsOrderMap).where(
            GrubOpsOrderMap.source_channel.in_(labels),
            GrubOpsOrderMap.external_id.in_(refs),
            GrubOpsOrderMap.mm_order_id.is_not(None),
        )
    )
    if map_row is None:
        return None
    return await db.scalar(
        select(Order)
        .where(Order.id == map_row.mm_order_id)
        .options(selectinload(Order.items))
    )


async def _agg_items(db: AsyncSession, agg_order_id) -> list[AggregatorOrderItem]:
    rows = await db.scalars(
        select(AggregatorOrderItem).where(
            AggregatorOrderItem.aggregator_order_id == agg_order_id
        )
    )
    return list(rows)


def _item_discrepancy(agg_items, mm_items: list[OrderItem]) -> tuple[dict | None, bool]:
    """Compare quantities per side. Line-grain only — an aggregate window carries
    no per-order breakdown to check, so it is reported as unknown, not a mismatch."""
    line_items = [
        i for i in agg_items if i.grain == GRAIN_LINE and i.quantity is not None
    ]
    if not line_items:
        return ({"note": "no line-grain aggregator items to compare"}, False)
    agg_qty = sum((i.quantity or Decimal(0)) for i in line_items)
    mm_qty = Decimal(sum(i.effective_quantity for i in mm_items))
    detail = {
        "agg_total_qty": str(agg_qty),
        "mm_total_qty": str(mm_qty),
        "agg_lines": len(line_items),
        "mm_lines": len(mm_items),
    }
    # Compare quantities, not line counts. `mm_items` carries every OrderItem
    # (modifiers, voided lines) so its length rarely equals the aggregator's line
    # grain even when the quantities agree — counting lines flagged good orders.
    flagged = abs(agg_qty - mm_qty) > _TOL
    return (detail, flagged)


#: A defensive guard for legacy synthetic item-aggregate rows. Deliveroo now
#: comes per-order through the Partner Hub provider (real order ids, line-grain
#: items), so nothing writes these any more; but an older `deliveroo-items:<window>`
#: carrier row (status `items_aggregate`) is not a real order — there is nothing
#: to match to an MM order and a `no_mm_order` flag on one would be noise — so
#: reconciliation skips any that remain.
_CARRIER_ORDER_PREFIX = "deliveroo-items:"
_CARRIER_ORDER_STATUS = "items_aggregate"


def _is_carrier_order(agg) -> bool:
    external = agg.external_order_id or ""
    return (
        external.startswith(_CARRIER_ORDER_PREFIX)
        or agg.status == _CARRIER_ORDER_STATUS
    )


async def reconcile_order(db: AsyncSession, agg, *, run_id=None) -> None:
    """Compute and upsert one reconciliation row for one aggregator order."""
    # A Deliveroo synthetic item-aggregate is not an order to reconcile — see
    # `_is_carrier_order`. Return before writing any reconciliation row.
    if _is_carrier_order(agg):
        return

    has_grubops = await _branch_has_grubops(db, agg.branch_id)

    mm_order = None
    if has_grubops:
        mm_order = await _find_mm_order(
            db, agg.channel, agg.external_order_id, agg.display_ref
        )
        match_status = MATCH_MATCHED if mm_order is not None else MATCH_UNMATCHED_AGG
    else:
        match_status = MATCH_NO_MAKER_SIDE

    flags: list[str] = []

    commission_actual = _d(agg.commission_amount)
    # No MODELLED expected commission any more: the shop keeps no static
    # configured commission rate to compare against (fees now come only from the
    # marketplace's own scraped statement). The overcharge-vs-contract variance
    # check went with it. `commission_actual` (the real cut the marketplace took)
    # and `commission_rate_effective` (that cut over the basket) are both scraped
    # and stay; `commission_expected`/`commission_variance` are left null.
    commission_expected = None
    commission_variance = None

    total_agg = _d(agg.gross_sales)
    total_mm = _d(mm_order.total) if mm_order else None
    amount_variance = (
        total_agg - total_mm if total_agg is not None and total_mm is not None else None
    )
    # A matched order whose totals disagree beyond tolerance is a real
    # discrepancy — flag it, so it both highlights in the dashboard and survives
    # the "flagged only" filter (which keys off `flags`), like commission/refund.
    if amount_variance is not None and abs(amount_variance) > _TOL:
        flags.append("amount_variance")
    rate_base = total_agg or total_mm
    rate_effective = None
    if commission_actual is not None and rate_base and rate_base != 0:
        rate_effective = (commission_actual / rate_base).quantize(Decimal("0.0001"))

    refund_agg = _d(agg.refund_amount)
    refund_mm = _d(mm_order.refunded_amount) if mm_order else None
    refund_flag = False
    if refund_agg is not None and refund_mm is not None:
        refund_flag = abs(refund_agg - refund_mm) > _TOL
        if refund_flag:
            flags.append("refund_mismatch")
    elif refund_agg and not refund_mm and mm_order is not None:
        refund_flag = True
        flags.append("refund_on_aggregator_only")

    item_discrepancy, item_flag = (None, False)
    if mm_order is not None:
        agg_items = await _agg_items(db, agg.id)
        item_discrepancy, item_flag = _item_discrepancy(agg_items, mm_order.items)
        if item_flag:
            flags.append("item_mismatch")

    # The `no_mm_order` flag means what it says: there is no MM order at all. On a
    # GrubOps branch with no maker match we still often have a recovery STANDALONE
    # (promotion filed one past the adopt grace), so the flag fires only when there
    # is genuinely nothing to point at — otherwise `match_status` alone carries the
    # "unmatched to a GrubOps order" story, without contradicting a populated link.
    if match_status == MATCH_UNMATCHED_AGG and agg.mm_order_id is None:
        flags.append("no_mm_order")

    values = {
        "channel": agg.channel,
        "external_order_id": agg.external_order_id,
        "branch_id": agg.branch_id,
        "aggregator_order_id": agg.id,
        # The MM order this row points at: the matched GrubOps maker order when
        # there is one, otherwise the order's OWN promoted MM order (the standalone
        # filed for an aggregator-only branch, or the recovery standalone on a
        # GrubOps branch GrubOps never ingested). Only a genuinely un-promoted order
        # (promotion still deferring) stays NULL — the honest "no MM order" the flag
        # above reports. `match_status` still tells the reconciliation story
        # separately, so a populated link never contradicts an unmatched verdict.
        "mm_order_id": mm_order.id if mm_order else agg.mm_order_id,
        "match_status": match_status,
        "item_discrepancy": item_discrepancy,
        "item_flag": item_flag,
        "refund_agg": refund_agg,
        "refund_mm": refund_mm,
        "refund_flag": refund_flag,
        "commission_expected": commission_expected,
        "commission_actual": commission_actual,
        "commission_variance": commission_variance,
        "commission_rate_effective": rate_effective,
        "total_agg": total_agg,
        "total_mm": total_mm,
        "amount_variance": amount_variance,
        "flags": flags or None,
        "run_id": run_id,
        "reconciled_at": utcnow(),
    }
    update = {
        k: v for k, v in values.items() if k not in ("channel", "external_order_id")
    }
    update["updated_at"] = utcnow()
    await db.execute(
        pg_insert(AggregatorReconciliation)
        .values(**values)
        .on_conflict_do_update(constraint="uq_aggregator_reconciliation", set_=update)
    )


async def reconcile_channel(db: AsyncSession, channel: str, *, run_id=None) -> int:
    """Reconcile the channel's new-or-changed orders. Returns rows written.

    Incremental, not a full re-scan: an order is (re)reconciled only when it has
    no reconciliation row yet, or its `updated_at` has advanced past the last
    `reconciled_at`. The daily pass therefore touches recent and changed orders
    rather than the whole history every time, while still catching a late
    statement — that re-upserts the order and bumps `updated_at` above the prior
    `reconciled_at`, pulling it back in. Idempotent and safe to re-run.
    """
    recon = AggregatorReconciliation
    orders = await db.scalars(
        select(AggregatorOrder)
        .outerjoin(
            recon,
            and_(
                recon.channel == AggregatorOrder.channel,
                recon.external_order_id == AggregatorOrder.external_order_id,
            ),
        )
        .where(
            AggregatorOrder.channel == channel,
            or_(
                recon.id.is_(None),
                AggregatorOrder.updated_at > recon.reconciled_at,
                # …and when promotion has (re)linked the MM order SINCE the last
                # reconcile. Promotion advances `promoted_at`, not `updated_at`
                # (which is the honest per-scrape change signal), so an order first
                # reconciled before its standalone MM order was filed would keep a
                # NULL `mm_order_id` on its recon row forever — the "no MM order" a
                # promoted aggregator-only order showed for. Re-selecting on
                # promoted_at copies the fresh link in. Converges: reconcile stamps
                # reconciled_at = now (> promoted_at), so the next pass skips it.
                AggregatorOrder.promoted_at > recon.reconciled_at,
                # Self-heal the rows the two clauses above cannot reach: an order
                # promoted BEFORE its last reconcile (so promoted_at < reconciled_at)
                # and never re-scraped since keeps whatever link that older pass
                # stored — NULL, for an aggregator-only order reconciled under the
                # logic that did not carry the standalone across. Re-select while the
                # order is linked but the recon row is not; converges the moment the
                # link is copied in (mm_order_id stops being NULL).
                and_(
                    recon.mm_order_id.is_(None),
                    AggregatorOrder.mm_order_id.isnot(None),
                ),
            ),
        )
    )
    count = 0
    for agg in orders:
        try:
            await reconcile_order(db, agg, run_id=run_id)
            count += 1
        except Exception:  # noqa: BLE001 — one order must not stop the pass
            logger.exception(
                "reconcile %s order %s failed", channel, agg.external_order_id
            )
    return count
