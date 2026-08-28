"""Operator maintenance for the aggregator scrape: purge to a clean slate.

Unlike migration `161_agg_clean_slate` (a one-shot at deploy time), this is a
callable service so an operator can reset the scraped data on demand — before a
full re-run over a date range, or to clear a bad pull — as many times as needed.

Two things are purged, and only these two:

1. **Promoted-only MM orders.** An MM order that exists ONLY because promotion
   created it — reachable from `aggregator_order.mm_order_id` and NOT owned by
   GrubOps (no `grubops_order_map` row). Barsha/Sharjah orders that promotion
   merely linked/overlaid onto a GrubOps order are GrubOps-owned and are KEPT.
2. **The scraped tables** (`aggregator_order` and friends), truncated.

Everything else is untouched: GrubOps/Foodics orders, website and counter orders,
and the aggregator config (`aggregator_account`/`_session`/`_branch_map`). A
re-scrape + re-promote rebuilds the scraped tables and re-files the DSO/Karama
orders; the convergence key means it never duplicates a kept GrubOps order.
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aggregator import AggregatorOrder
from app.models.grubops_order import GrubOpsOrderMap
from app.models.order import Order

logger = logging.getLogger(__name__)

#: Scraped tables to truncate — same set as migration 161. One TRUNCATE, so FK
#: order does not matter; CASCADE only reaches rows inside this set (order_item /
#: reconciliation FK back to aggregator_order), never `orders`, GrubOps or
#: Foodics. Config tables (account/session/branch_map) are deliberately absent.
_SCRAPED_TABLES = (
    "aggregator_reconciliation",
    "aggregator_statement_line",
    "aggregator_order_item",
    "aggregator_payout",
    "aggregator_statement",
    "aggregator_order",
    "aggregator_sync_run",
)


def _promoted_only_order_ids():
    """SELECT of MM order ids created by promotion alone (safe to delete).

    = linked from `aggregator_order.mm_order_id` AND NOT GrubOps-owned. The
    GrubOps exclusion is load-bearing: for Barsha/Sharjah `aggregator_order`
    points at an order GrubOps owns, and deleting that would destroy a GrubOps
    order (a real order the shop fulfilled), not a promotion artefact.
    """
    promoted = select(AggregatorOrder.mm_order_id).where(
        AggregatorOrder.mm_order_id.is_not(None)
    )
    grubops_owned = select(GrubOpsOrderMap.mm_order_id).where(
        GrubOpsOrderMap.mm_order_id.is_not(None)
    )
    return select(Order.id).where(
        Order.id.in_(promoted), Order.id.not_in(grubops_owned)
    )


async def count_purge_targets(db: AsyncSession) -> dict:
    """Dry-run: how many rows a purge would remove. No writes."""
    order_ids = (await db.scalars(_promoted_only_order_ids())).all()
    counts = {"promoted_only_orders": len(order_ids)}
    for tbl in _SCRAPED_TABLES:
        counts[tbl] = await db.scalar(text(f"SELECT count(*) FROM {tbl}")) or 0
    return counts


async def purge_scraped_data(
    db: AsyncSession, *, delete_promoted_orders: bool = True
) -> dict:
    """Reset the scraped aggregator data (and, by default, promoted-only MM orders)
    to a clean slate. Returns what was removed. The caller commits.

    `delete_promoted_orders=False` truncates the scraped tables only and leaves
    the promoted MM orders in place (they re-converge on the next promote) — the
    exact behaviour of migration 161, for when you want to keep the order history.
    """
    stats: dict = {}
    if delete_promoted_orders:
        ids = (await db.scalars(_promoted_only_order_ids())).all()
        if ids:
            # DB-level FK CASCADE removes the order children (items, payments,
            # status events, …); the aggregator/grubops links to these ids are
            # ON DELETE SET NULL, and the scraped rows are about to be truncated
            # anyway.
            await db.execute(delete(Order).where(Order.id.in_(ids)))
        stats["promoted_orders_deleted"] = len(ids)
        logger.info("purge: deleted %d promotion-only MM orders", len(ids))

    await db.execute(
        text(
            "TRUNCATE TABLE " + ", ".join(_SCRAPED_TABLES) + " RESTART IDENTITY CASCADE"
        )
    )
    stats["scraped_tables_truncated"] = list(_SCRAPED_TABLES)
    logger.info("purge: truncated scraped tables %s", ", ".join(_SCRAPED_TABLES))
    return stats
