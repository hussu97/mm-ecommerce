"""Reconcile aggregator order money to the authoritative GrubTech push.

Promotion gap-fills a Barsha/Sharjah order from the Careem/Noon statement scrape,
then the GrubOps push adopts that row — and adoption used to keep the order without
applying the push money. Two consequences show in the books:

* Careem's scraped ``gross_sales`` is net of its own menu markup, so the order
  total read low (e.g. 169639854 booked 75 for a 100 sale, 169360721 booked 40 for
  70) — the AED-55 "Careem gap" was exactly these.
* The loyalty/promo discount lives only on the push (``discountAmount``), so an
  adopted Noon order dropped it (0882 booked total 50 with discount 0 instead of a
  70 gross less a 20 discount).

The GrubTech push is the POS record of what the customer actually paid, so this
restates the money columns from ``grubops_order_map.raw`` for every aggregator
order whose stored total/discount disagrees with it. Guarded to touch only rows
that differ, so a correctly-booked order (Talabat, whose push total was already
right, and every clean order) is untouched — including on a restored dump. VAT is
re-derived inclusive at 5% to match ``money_fields_from_info``/migration 196.

Orders with no push raw (promotion-owned DSO/Karama) are deliberately left alone:
without the push we cannot tell a menu-gross from a discounted net, so those few
are handled out of band, not guessed at here.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "197_agg_push_money"
down_revision: Union[str, None] = "196_backfill_aggregator_vat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE orders o SET
            total = (gom.raw->'orderHeader'->>'totalPrice')::numeric,
            discount_amount = COALESCE((gom.raw->'orderHeader'->>'discountAmount')::numeric, 0),
            subtotal = (gom.raw->'orderHeader'->>'totalPrice')::numeric
                       + COALESCE((gom.raw->'orderHeader'->>'discountAmount')::numeric, 0),
            total_excl_vat = round((gom.raw->'orderHeader'->>'totalPrice')::numeric / 1.05, 2),
            vat_amount = (gom.raw->'orderHeader'->>'totalPrice')::numeric
                         - round((gom.raw->'orderHeader'->>'totalPrice')::numeric / 1.05, 2),
            vat_rate = 0.05
        FROM grubops_order_map gom
        WHERE gom.mm_order_id = o.id
          AND o.source = 'aggregator'
          AND o.status <> 'cancelled'
          AND (gom.raw->'orderHeader'->>'totalPrice') IS NOT NULL
          AND (
              o.total IS DISTINCT FROM (gom.raw->'orderHeader'->>'totalPrice')::numeric
              OR o.discount_amount IS DISTINCT FROM
                 COALESCE((gom.raw->'orderHeader'->>'discountAmount')::numeric, 0)
          )
        """
    )


def downgrade() -> None:
    # A one-way correction to match the authoritative POS record; there is no
    # faithful reversal.
    pass
