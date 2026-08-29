"""Backfill order payment_method: real tender for counter + card for aggregator.

Two long-standing display bugs, both a content backfill the deploy has to carry
(CLAUDE.md §7), both guarded so they match only the buggy state and so leave an
admin's later edit — or a re-run — untouched.

1. **Counter orders** left `payment_method` empty ("unknown") even though the
   cashier picked a method, because the tender lived only on the `order_payments`
   rows. Backfill each cashier order from its non-refund payments: the method's
   type when they agree, `mixed` when they do not. The code now stamps this at
   payment time, so this only catches the orders taken before that landed.
   Guarded to `source='cashier'` rows whose `payment_method` is still null/empty.

2. **Aggregator orders** were all filed as `cod` on purpose once, but that read as
   "cash on delivery" on every marketplace order in the console when almost all
   are prepaid card. Set the true tender from the payment type we already capture
   (`aggregator_payment_type`): `postpaid` → `cod`, everything else (prepaid or
   unknown) → `card`. Guarded to `source='aggregator'` rows still at `cod`, so a
   genuine cash order re-derives to `cod` (a no-op) and nothing an admin changed
   to something other than `cod` is touched.

Revision ID: 164_payment_backfill
Revises: 163_agg_enrich_status
Create Date: 2026-08-29
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "164_payment_backfill"
down_revision: Union[str, None] = "163_agg_enrich_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Counter orders → the tender the cashier actually recorded.
    op.execute(
        """
        UPDATE orders o
        SET payment_method = sub.pm_type
        FROM (
            SELECT op.order_id,
                   CASE WHEN count(DISTINCT pm.type) > 1
                        THEN 'mixed'
                        ELSE max(pm.type)
                   END AS pm_type
            FROM order_payments op
            JOIN payment_methods pm ON pm.id = op.payment_method_id
            WHERE op.is_refund = false
            GROUP BY op.order_id
        ) AS sub
        WHERE o.id = sub.order_id
          AND o.source = 'cashier'
          AND (o.payment_method IS NULL OR o.payment_method = '')
        """
    )

    # 2. Aggregator orders → card by default, cod only where we know it was cash.
    op.execute(
        """
        UPDATE orders
        SET payment_method = CASE
                WHEN aggregator_payment_type = 'postpaid' THEN 'cod'
                ELSE 'card'
            END
        WHERE source = 'aggregator'
          AND payment_method = 'cod'
        """
    )


def downgrade() -> None:
    # A reporting backfill; the prior state was an empty counter tender and a flat
    # aggregator `cod`, neither worth reconstructing. No-op.
    pass
