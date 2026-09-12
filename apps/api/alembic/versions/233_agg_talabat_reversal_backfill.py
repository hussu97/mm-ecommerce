"""Backfill Talabat item-level reversals onto the ledger.

Until now the Talabat sales scrape dropped the "Operational Charges" /
"Vendor Refunds" columns on the floor, so a delivered order the customer was
partially refunded on (a missing/wrong item Talabat compensates and bills back
to the vendor) was promoted at its full gross — the sale read too high by the
refunded amount. The provider now captures that as ``aggregator_order.refund_amount``
and promotion books it onto ``orders.refunded_amount`` (which net revenue
subtracts), but the rows already scraped carry no ``refund_amount`` and their
promoted orders still show the full sale.

This backfills both, from the raw CSV Talabat itself preserved, in two guarded
passes so a replay — or a run against a console-corrected database — matches
nothing:

1. ``aggregator_order.refund_amount`` from ``Operational Charges + Vendor Refunds``
   (capped at the gross, cancelled orders excluded — their whole value is handled
   by the cancellation path), only where it is still ``NULL``.
2. ``orders.refunded_amount`` / ``refunded_at`` from that reversal, only where the
   promoted order still records no refund (``refunded_amount = 0``).

As of 2026-09-12 this touches exactly one order — Al Karama #3879859531
(AGG-20260909-017), a 70.00 missing-item reversal on a 140.00 sale — but it is
written generically so any other historical Talabat reversal is corrected too.
"""

from __future__ import annotations

from alembic import op

revision = "233_agg_talabat_reversal_bf"
down_revision = "232_meydan_area_lala"
branch_labels = None
depends_on = None

# Reversal money Talabat states on the order row: the missing/wrong-item
# chargeback plus any vendor-agreed refund, from the preserved CSV.
_REVERSAL = (
    "COALESCE((raw->>'Operational Charges')::numeric, 0) "
    "+ COALESCE((raw->>'Vendor Refunds')::numeric, 0)"
)


def upgrade() -> None:
    # 1) Capture the reversal onto the aggregator row (fill-only).
    op.execute(
        f"""
        UPDATE aggregator_order
           SET refund_amount = LEAST({_REVERSAL}, COALESCE(gross_sales, {_REVERSAL}))
         WHERE channel = 'talabat'
           AND refund_amount IS NULL
           AND lower(COALESCE(status, '')) <> 'cancelled'
           AND {_REVERSAL} > 0
        """
    )
    # 2) Book it onto the promoted order (only where nothing is recorded yet).
    op.execute(
        """
        UPDATE orders o
           SET refunded_amount = ao.refund_amount,
               refunded_at = COALESCE(ao.delivered_at, ao.placed_at, now())
          FROM aggregator_order ao
         WHERE ao.mm_order_id = o.id
           AND ao.channel = 'talabat'
           AND ao.refund_amount IS NOT NULL
           AND ao.refund_amount > 0
           AND COALESCE(o.refunded_amount, 0) = 0
        """
    )


def downgrade() -> None:
    # Revert only rows still exactly equal to what this migration set, so a
    # console edit made in between is never clobbered.
    op.execute(
        """
        UPDATE orders o
           SET refunded_amount = 0,
               refunded_at = NULL
          FROM aggregator_order ao
         WHERE ao.mm_order_id = o.id
           AND ao.channel = 'talabat'
           AND ao.refund_amount IS NOT NULL
           AND o.refunded_amount = ao.refund_amount
        """
    )
    op.execute(
        f"""
        UPDATE aggregator_order
           SET refund_amount = NULL
         WHERE channel = 'talabat'
           AND refund_amount = LEAST({_REVERSAL}, COALESCE(gross_sales, {_REVERSAL}))
           AND {_REVERSAL} > 0
        """
    )
