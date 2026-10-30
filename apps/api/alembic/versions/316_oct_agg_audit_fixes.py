"""Correct the orders the Oct 2026 aggregator audit found mis-booked.

noon. The OMS parser took `estimatedDeliveryAt`, an ETA noon keeps after a
cancel, as the delivery time. With a delivery time and no cancel time, promote
read every noon cancellation as a refund after delivery: it labelled the order
"Refunded after delivery" and climbed it to delivered first (9 orders, 5-29 Oct
2026). The parser now stamps a delivery time only on a delivered order. This
clears the ETA on the cancelled marketplace rows, which a re-scrape would never
null (`delivered_at` is preserve-if-null), and clears the label and the
delivery time on the MM orders. The stock those orders drew is left: counts
taken since have already absorbed it.

Talabat. Two delivered orders carried the wrong refund:
* 3946944067: Talabat refunded the customer 30 at its own cost and paid it to
  us ("Vendor Refunds"). It was booked as our refund, and the gross was the
  reported 40 instead of the 70 sold.
* 3959129572: the refund is the 40 of "Operational Charges". Adding "Amount
  owed back to Talabat" (the 8.79 shortfall of its negative payout) pushed it
  to the 45 cap.

Guarded: each statement matches the exact wrong value, so it is a no-op once
corrected (by this or a re-ingest) and on a database restored from an older
dump writes only what the fixed parser would. The downgrade restores those
values.

Revision ID: 316_oct_agg_audit_fixes
Revises: 315_branch_busy_mode
Create Date: 2026-10-30
"""

from typing import Sequence, Union

from alembic import op

revision: str = "316_oct_agg_audit_fixes"
down_revision: Union[str, None] = "315_branch_busy_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOON_CANCELLED = (
    "a.channel = 'noon' "
    "AND lower(a.status) IN ('canceled', 'cancelled', 'rejected', 'failed', 'declined')"
)


def upgrade() -> None:
    # MM orders first: they are found through the marketplace row's ETA.
    op.execute(
        f"""
        UPDATE orders AS o
           SET aggregator_cancel_reason = NULL,
               delivered_at = NULL
          FROM aggregator_order AS a
         WHERE a.mm_order_id = o.id
           AND {_NOON_CANCELLED}
           AND a.delivered_at IS NOT NULL
           AND o.source = 'aggregator'
           AND o.status = 'cancelled'
           AND o.aggregator_cancel_reason = 'Refunded after delivery'
        """
    )
    op.execute(
        f"""
        UPDATE aggregator_order AS a
           SET delivered_at = NULL
         WHERE {_NOON_CANCELLED}
           AND a.delivered_at IS NOT NULL
        """
    )

    op.execute(
        """
        UPDATE aggregator_order
           SET gross_sales = 70.00, net_sales = 70.00, refund_amount = NULL
         WHERE channel = 'talabat' AND external_order_id = '3946944067'
           AND gross_sales = 40.00 AND refund_amount = 30.00
        """
    )
    op.execute(
        """
        UPDATE orders AS o
           SET refunded_amount = 0, refunded_at = NULL
          FROM aggregator_order AS a
         WHERE a.mm_order_id = o.id
           AND a.channel = 'talabat' AND a.external_order_id = '3946944067'
           AND o.refunded_amount = 30.00
        """
    )
    op.execute(
        """
        UPDATE aggregator_order
           SET refund_amount = 40.00
         WHERE channel = 'talabat' AND external_order_id = '3959129572'
           AND refund_amount = 45.00
        """
    )
    op.execute(
        """
        UPDATE orders AS o
           SET refunded_amount = 40.00
          FROM aggregator_order AS a
         WHERE a.mm_order_id = o.id
           AND a.channel = 'talabat' AND a.external_order_id = '3959129572'
           AND o.refunded_amount = 45.00
        """
    )


def downgrade() -> None:
    # The noon ETAs are not kept anywhere to restore; the next scrape of a
    # still-listed order would not restore them either (the parser no longer
    # stamps one on a cancel). Only the Talabat figures are put back.
    op.execute(
        """
        UPDATE orders AS o
           SET refunded_amount = 45.00
          FROM aggregator_order AS a
         WHERE a.mm_order_id = o.id
           AND a.channel = 'talabat' AND a.external_order_id = '3959129572'
           AND o.refunded_amount = 40.00
        """
    )
    op.execute(
        """
        UPDATE aggregator_order
           SET refund_amount = 45.00
         WHERE channel = 'talabat' AND external_order_id = '3959129572'
           AND refund_amount = 40.00
        """
    )
    op.execute(
        """
        UPDATE orders AS o
           SET refunded_amount = 30.00, refunded_at = a.delivered_at
          FROM aggregator_order AS a
         WHERE a.mm_order_id = o.id
           AND a.channel = 'talabat' AND a.external_order_id = '3946944067'
           AND o.refunded_amount = 0
        """
    )
    op.execute(
        """
        UPDATE aggregator_order
           SET gross_sales = 40.00, net_sales = 40.00, refund_amount = 30.00
         WHERE channel = 'talabat' AND external_order_id = '3946944067'
           AND gross_sales = 70.00 AND refund_amount IS NULL
        """
    )
