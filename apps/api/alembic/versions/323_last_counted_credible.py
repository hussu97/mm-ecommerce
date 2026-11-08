"""Re-derive `inventory_levels.last_counted_at` from counts that were real.

Until this release a POS stock report could only be submitted with every row
filled, so the shop typed the system figure into every row it had not counted.
Each of those rows posted as an inventory count and stamped `last_counted_at`,
so the stamp said "counted today" for items nobody had looked at: in the 30 days
to 2026-11-08, 91% of report count rows equalled the system figure exactly.
Rows are now skippable, and only a counted row stamps the level — but the stamps
already written would keep hiding stale items until they aged out.

The owner chose to keep only counts that look real:
- a POS report row whose entered figure differs from the report's system
  (expected) figure — a copied figure is indistinguishable from an exact count,
  and treating it as uncounted errs toward asking for a count;
- every admin stock audit / manual count (anything not from a POS report),
  including opening balances, which `post_transaction` did not stamp before.

Levels with no such count go to NULL ("never counted"). Only `inventory_levels`
is written; the closed ledger rows are read, never changed.

Revision ID: 323_last_counted_credible
Revises: 322_mm_cafe_rebrand
Create Date: 2026-11-08
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "323_last_counted_credible"
down_revision: Union[str, None] = "322_mm_cafe_rebrand"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        WITH credible AS (
            SELECT ti.item_id, t.warehouse_id, max(t.posted_at) AS counted_at
            FROM inventory_transaction_items ti
            JOIN inventory_transactions t ON t.id = ti.transaction_id
            LEFT JOIN shift_inventory_report_lines srl
              ON t.source_type = 'shift_inventory_report'
             AND srl.report_id::text = t.source_id
             AND srl.item_id = ti.item_id
            WHERE t.type IN ('inventory_count', 'opening_balance')
              AND t.status = 'closed'
              AND (
                    t.source_type IS DISTINCT FROM 'shift_inventory_report'
                 OR (srl.entered_quantity IS NOT NULL
                     AND srl.entered_quantity IS DISTINCT FROM srl.expected_quantity)
              )
            GROUP BY ti.item_id, t.warehouse_id
        )
        UPDATE inventory_levels l
           SET last_counted_at = c.counted_at
          FROM inventory_levels l2
          LEFT JOIN credible c
            ON c.item_id = l2.item_id AND c.warehouse_id = l2.warehouse_id
         WHERE l.id = l2.id
           AND l.last_counted_at IS DISTINCT FROM c.counted_at
        """
    )


def downgrade() -> None:
    # The overwritten stamps were the misleading ones; there is nothing worth
    # restoring, and the ledger still holds every count.
    pass
