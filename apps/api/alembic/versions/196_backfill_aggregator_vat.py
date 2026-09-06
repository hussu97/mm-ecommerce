"""Backfill output VAT on aggregator orders that were filed with zero VAT.

Aggregator orders trusted the provider payload's tax figure and booked ``0`` when
it was absent — roughly one delivered sale in four. A UAE storefront price is
VAT-inclusive and every catalogue item is standard-rated, so the correct output
VAT on a delivered aggregator sale is ``total * 5/105`` — the same inclusive 5%
the counter/website derive (``order_pricing.VAT_RATE``) and that the aggregator
code now derives too.

The VAT report sums ``order_taxes`` (``pos_reports.financial``), not
``orders.vat_amount``, so the actual understatement was the **832** orders (of
1,021 with a zero ``vat_amount`` column) that had **no VAT tax row at all** —
about AED 2,127 of output VAT missing from the report since ingest went live
(2026-07-30). The other 189 already had a real VAT tax row (the report counted
them); only their ``vat_amount`` column was stale.

So this: (1) inserts the missing VAT tax row, derived as
``net = round(total/1.05, 2)`` then ``vat = total - net`` — identical to
``pos_pricing.split_inclusive_tax``, so a backfilled row equals a freshly filed
one; then (2) re-derives ``orders.vat_amount`` / ``total_excl_vat`` **from the
order's VAT tax rows**, so the column and the rows always agree (for the 189 that
keeps their existing provider figure; for the 832 it adopts the one just
inserted).

Guarded so it only ever touches a row still at zero VAT: once corrected here (or
by an admin) it matches nothing, including on a database restored from an older
dump. Cancelled orders are left untouched — a cancelled order is not a sale and
owes no output VAT.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "196_backfill_aggregator_vat"
down_revision: Union[str, None] = "195_report_template_order"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # (1) The missing per-order VAT tax row — inserted only where the order has
    # none yet, so an order that already carries one is never duplicated and its
    # own (provider-reported) figure is preserved.
    op.execute(
        """
        INSERT INTO order_taxes
            (id, order_id, tax_id, name, rate, taxable_amount, amount,
             created_at, updated_at)
        SELECT gen_random_uuid(), o.id, NULL, 'VAT', 0.05,
               round(o.total / 1.05, 2), o.total - round(o.total / 1.05, 2),
               now(), now()
        FROM orders o
        WHERE o.source = 'aggregator'
          AND o.status <> 'cancelled'
          AND o.total > 0
          AND (o.vat_amount = 0 OR o.vat_amount IS NULL)
          AND NOT EXISTS (
              SELECT 1 FROM order_taxes t
              WHERE t.order_id = o.id AND t.name = 'VAT'
          )
        """
    )
    # (2) Re-derive the order's VAT columns FROM its tax rows, so the column that
    # was zero now equals the sum the VAT report actually reads. Runs after (1), so
    # every target order has its rows in place.
    op.execute(
        """
        UPDATE orders o
        SET vat_amount = tx.vat,
            total_excl_vat = o.total - tx.vat,
            vat_rate = 0.05
        FROM (
            SELECT order_id, sum(amount) AS vat
            FROM order_taxes
            WHERE name = 'VAT'
            GROUP BY order_id
        ) tx
        WHERE o.id = tx.order_id
          AND o.source = 'aggregator'
          AND o.status <> 'cancelled'
          AND o.total > 0
          AND (o.vat_amount = 0 OR o.vat_amount IS NULL)
        """
    )


def downgrade() -> None:
    # A one-way data correction — there is no faithful "un-correct" to run, and
    # zeroing the VAT back would reintroduce the understatement.
    pass
