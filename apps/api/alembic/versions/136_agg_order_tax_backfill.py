"""Give aggregator orders their VAT row, so the receipt shows the tax.

The order-ingest set `vat_amount` / `vat_rate` / `total_excl_vat` on the order
but wrote no `order_taxes` row, and the register receipt renders its VAT line
from `order_taxes` — so an aggregator receipt showed no tax at all. The ingest
now writes one VAT row (the same shape a website order does); this backfills the
orders already recorded without one.

Guarded: only an aggregator order that has VAT and no tax row yet gets one, so a
re-run does nothing.

Revision ID: 136_agg_order_tax
Revises: 135_agg_option_keys
Create Date: 2026-08-23
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "136_agg_order_tax"
down_revision: Union[str, None] = "135_agg_option_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO order_taxes
            (id, order_id, tax_id, name, rate, taxable_amount, amount,
             created_at, updated_at)
        SELECT gen_random_uuid(), o.id, NULL, 'VAT',
               o.vat_rate, o.total_excl_vat, o.vat_amount, now(), now()
        FROM orders o
        WHERE o.source = 'aggregator'
          AND o.vat_amount > 0
          AND NOT EXISTS (
              SELECT 1 FROM order_taxes t WHERE t.order_id = o.id
          )
        """
    )


def downgrade() -> None:
    # The rows are correct; reversing this only to blank the receipt again helps
    # nothing.
    pass
