"""Correct Careem order gross that was taken from `price.sub_total`, not the menu total.

`careem_provider` mapped `aggregator_order.gross_sales` from
`_first(price, "sub_total", "total", "original")`. On a CPlus order Careem reports
a `sub_total` BELOW the menu value the customer actually paid (e.g. 93.75 on a 125
order whose two items are 70 + 55, and whose settlement bills commission on 125),
so our gross was understated. The provider now prefers `total`/`original`; this
repairs the rows already stored the old way.

Guarded and idempotent: only rows where `sub_total < total` (the bug) and whose
`gross_sales` still equals neither the corrected total — so once fixed, or on a
restored dump, it matches nothing. `updated_at` is bumped so a re-promote inside
the window lifts the MM order total to match (older orders outside the promote
window keep their recorded total; the aggregator figure — what the settlement
reconciliation reads — is corrected regardless). Across prod today: 5 orders,
+125.25 of gross.

Revision ID: 254_careem_gross_total
Revises: 253_settlement_backfill
Create Date: 2026-09-17
"""

from typing import Sequence, Union

from alembic import op

revision: str = "254_careem_gross_total"
down_revision: Union[str, None] = "253_settlement_backfill"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE aggregator_order o
        SET gross_sales = (o.raw->'price'->>'total')::numeric,
            updated_at = now()
        WHERE o.channel = 'careem'
          AND o.raw->'price'->>'total' IS NOT NULL
          AND o.raw->'price'->>'sub_total' IS NOT NULL
          AND (o.raw->'price'->>'total')::numeric > (o.raw->'price'->>'sub_total')::numeric
          AND o.gross_sales IS DISTINCT FROM (o.raw->'price'->>'total')::numeric
        """
    )


def downgrade() -> None:
    # A data correction; the menu total is the true goods value, nothing to reverse.
    pass
