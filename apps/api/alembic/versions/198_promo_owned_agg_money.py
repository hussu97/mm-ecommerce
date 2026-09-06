"""Reconcile the promotion-owned aggregator orders the push fix could not reach.

Migration 197 corrected the money on GrubOps-adopted orders from the authoritative
GrubTech push. A handful of aggregator orders have no push at all — they were filed
purely from a marketplace scrape on a promotion-owned outlet (DSO / Al Karama) —
and two came out with a header total below their own line items. They need opposite
corrections, by channel, because the gap means different things:

* **Careem** charges the customer the menu price, but its scraped ``gross_sales`` is
  net of its own menu markup — so the header read low (168889697 booked 63 for a 90
  menu order). The lines carry the true menu price; raise the total to the line sum.
* **Noon** genuinely discounts (loyalty/promo): the customer paid the lower net, and
  the gap IS the discount (FG8R… booked 50 against a 70 gross). Keep the net total
  and record the ``gross − net`` as the discount, so gross − discount = net holds.

Guarded to promotion-owned rows (no ``grubops_order_map``) whose line sum still
exceeds the stored total and that carry no discount yet — so it matches only these
few and nothing once corrected, including on a restored dump.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "198_promo_owned_agg_money"
down_revision: Union[str, None] = "197_agg_push_money"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The condition that identifies a promotion-owned order whose header total fell
# below its own line items — shared by both channel corrections.
_WHERE = """
    o.source = 'aggregator'
    AND o.status <> 'cancelled'
    AND o.discount_amount = 0
    AND NOT EXISTS (SELECT 1 FROM grubops_order_map g WHERE g.mm_order_id = o.id)
    AND ls.line_sum > o.total
"""


def upgrade() -> None:
    # Careem: raise the header to the menu line sum (customer paid the full menu).
    op.execute(
        f"""
        UPDATE orders o SET
            total = ls.line_sum,
            subtotal = ls.line_sum,
            total_excl_vat = round(ls.line_sum / 1.05, 2),
            vat_amount = ls.line_sum - round(ls.line_sum / 1.05, 2),
            vat_rate = 0.05
        FROM (SELECT order_id, sum(total_price) AS line_sum FROM order_items GROUP BY order_id) ls
        WHERE o.id = ls.order_id
          AND o.aggregator_channel = 'Careem'
          AND {_WHERE}
        """
    )
    # Noon: keep the discounted net; record gross − net as the loyalty discount.
    op.execute(
        f"""
        UPDATE orders o SET
            discount_amount = ls.line_sum - o.total,
            subtotal = ls.line_sum
        FROM (SELECT order_id, sum(total_price) AS line_sum FROM order_items GROUP BY order_id) ls
        WHERE o.id = ls.order_id
          AND o.aggregator_channel IN ('Noon Food', 'Noon')
          AND {_WHERE}
        """
    )


def downgrade() -> None:
    # One-way correction; no faithful reversal.
    pass
