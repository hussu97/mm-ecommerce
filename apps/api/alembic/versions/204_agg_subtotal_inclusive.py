"""Put every aggregator order's `subtotal` on the same VAT-INCLUSIVE base.

Every order writer books `subtotal` VAT-inclusive (the gross line sum) — except
promotion, which alone wrote the VAT-EXCLUSIVE figure (`total_excl_vat`) there. So
a report summing `orders.subtotal` across a channel mixed an inclusive base with an
exclusive one and silently understated the promoted orders by 5% (F-AGG-6). The
code fix makes promotion write `subtotal = total`; this repairs the rows already
stored the old way.

Guarded so it cannot fight anything: it matches only aggregator-source rows whose
`subtotal` still equals `total_excl_vat` (the ex-VAT figure promotion wrote) AND
whose ex-VAT figure differs from `total` (i.e. VAT was actually charged), and sets
`subtotal = total`. A genuinely discounted order already carries its gross on
`subtotal` (≠ `total_excl_vat`) and is untouched; a zero-VAT order has all three
equal and needs nothing. Idempotent: once corrected, `subtotal = total ≠
total_excl_vat`, so a re-run — or a run over a restored dump — matches nothing.

Revision ID: 203_agg_subtotal_inclusive
Revises: 202_order_stock_drawn
Create Date: 2026-09-07
"""

from typing import Sequence, Union

from alembic import op

revision: str = "204_agg_subtotal_inclusive"
down_revision: Union[str, None] = "203_order_stock_drawn"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE orders
        SET subtotal = total
        WHERE source = 'aggregator'
          AND subtotal = total_excl_vat
          AND total_excl_vat <> total
        """
    )


def downgrade() -> None:
    # One-way correction: the VAT-exclusive figure it replaced is still on
    # `total_excl_vat`, but re-splitting `subtotal` back to it would re-introduce the
    # mixed-base bug, so there is no faithful reversal.
    pass
