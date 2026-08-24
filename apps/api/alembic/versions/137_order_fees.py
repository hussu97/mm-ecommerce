"""Store what each order costs us, and what each marketplace takes.

Two deductions arrive with an order rather than with the cake: whoever carried
it takes a cut, and whoever took the money takes a fee. Neither was written
down. `order_economics` recomputed the card fee on every page view and the
marketplace commission was not modelled at all, so a Talabat order and a website
order sat in the same table looking equally profitable while one of them was a
quarter lighter — and there was no column to sum, so no profit-and-loss could be
asked for at all.

Three parts, and the second is the reason for the first:

1. `couriers.commission_percent` / `payment_fee_percent` — the contract rates,
   per channel, editable in the console. Both quoted **before VAT**, the way the
   contracts are written and the invoices arrive; `order_fees` adds the 5%.
2. `orders.aggregator_fee` / `payment_fee` — what those rates came to on *this*
   order, stamped when its total went final. Stored rather than derived so a
   renegotiated rate cannot silently rewrite the margin on every order the shop
   has already taken.
3. A backfill of both columns for the orders already in the table, so the new
   screens do not start with a hole where last month was.

**Only Noon Food's rates are seeded**, because they are the only ones agreed:
25% commission and 2% payment, both before VAT and neither carrying a flat part. Talabat, Deliveroo, Careem and
Keeta stay null until somebody supplies theirs — and null is load-bearing here.
It reads as "we do not know", leaves the order's fee null, and makes the screens
say "not itemised". Seeding a zero instead would tell the shop it keeps every
dirham of a Talabat order, which is the exact error this migration exists to
correct.

Guarded so it cannot fight the console: the rate seed only fills a NULL, and the
backfill only fills orders whose fee columns are still NULL. Once a human edits
a rate or a later stamp writes a fee, this matches nothing and does nothing —
including on a database restored from an older dump.

Revision ID: 137_order_fees
Revises: 136_agg_order_tax
Create Date: 2026-08-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "137_order_fees"
down_revision: Union[str, None] = "136_agg_order_tax"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. The rates ─────────────────────────────────────────────────────────
    # Each fee is a **pair**: a percentage of the basket and a flat amount on
    # top of it. Several of these contracts are quoted as "25% plus two dirhams
    # an order", the same shape a card processor's fee has always had here
    # (`payment_gateways.fee_percent` + `fee_fixed`), and a percentage-only
    # column would silently drop half of every such sentence.
    op.add_column(
        "couriers", sa.Column("commission_percent", sa.Numeric(5, 2), nullable=True)
    )
    op.add_column(
        "couriers", sa.Column("commission_fixed", sa.Numeric(10, 2), nullable=True)
    )
    op.add_column(
        "couriers", sa.Column("payment_fee_percent", sa.Numeric(5, 2), nullable=True)
    )
    op.add_column(
        "couriers", sa.Column("payment_fee_fixed", sa.Numeric(10, 2), nullable=True)
    )

    # Noon Food, the only contract whose numbers the shop has confirmed.
    op.execute(
        """
        UPDATE couriers
           SET commission_percent = 25.00,
               payment_fee_percent = 2.00
         WHERE code = 'noon_food'
           AND commission_percent IS NULL
           AND payment_fee_percent IS NULL
        """
    )

    # ── 2. The per-order columns ─────────────────────────────────────────────
    op.add_column(
        "orders", sa.Column("aggregator_fee", sa.Numeric(10, 2), nullable=True)
    )
    op.add_column("orders", sa.Column("payment_fee", sa.Numeric(10, 2), nullable=True))

    # ── 3. The backfill ──────────────────────────────────────────────────────
    #
    # Deliberately SQL rather than a script somebody has to remember to run: a
    # margin screen that is blank for every order older than the deploy is a
    # screen nobody trusts afterwards, however right it is about today.
    #
    # The arithmetic mirrors `order_fees.compute` exactly, and the two must move
    # together. If that module's rules change, this migration stays as it is —
    # it describes what the fees were on the day it ran, which is the only thing
    # a historic row can honestly say.

    # Aggregator orders: commission and payment fee off the channel's rates.
    # Joined through the same channel-name matching `courier_catalog` does —
    # `aggregator_channel` holds a display name ("Keeta 2.0", "Noon Food") and
    # `couriers.code` holds a slug, so the join is on the family prefix.
    #
    # A fee is unknown only when **both** halves of its pair are null. One half
    # set and the other null is a contract that quotes only a percentage, or
    # only a flat amount, and the missing half is genuinely nothing.
    op.execute(
        """
        UPDATE orders o
           SET aggregator_fee = CASE
                   WHEN c.commission_percent IS NULL AND c.commission_fixed IS NULL
                        THEN NULL
                   ELSE round(
                       (o.total * coalesce(c.commission_percent, 0) / 100
                        + coalesce(c.commission_fixed, 0)) * 1.05, 2)
               END,
               payment_fee = CASE
                   WHEN c.payment_fee_percent IS NULL AND c.payment_fee_fixed IS NULL
                        THEN NULL
                   ELSE round(
                       (o.total * coalesce(c.payment_fee_percent, 0) / 100
                        + coalesce(c.payment_fee_fixed, 0)) * 1.05, 2)
               END
          FROM couriers c
         WHERE o.source = 'aggregator'
           AND o.aggregator_fee IS NULL
           AND o.payment_fee IS NULL
           AND c.is_aggregator IS TRUE
           AND lower(o.aggregator_channel) LIKE
               CASE c.code WHEN 'noon_food' THEN 'noon%' ELSE c.code || '%' END
        """
    )

    # Card orders on our own channels: the gateway's published rate plus VAT.
    op.execute(
        """
        UPDATE orders o
           SET payment_fee = round(
                   (o.total * g.fee_percent / 100 + g.fee_fixed) * 1.05, 2
               )
          FROM payment_gateways g
         WHERE o.source <> 'aggregator'
           AND o.payment_fee IS NULL
           AND o.payment_method = 'card'
           AND o.total > 0
           AND g.code = o.payment_provider
        """
    )

    # Everything else that took no card took no fee, and zero is the true
    # answer there rather than the "we do not know" a null would claim: a cash
    # sale at the counter paid no processor.
    op.execute(
        """
        UPDATE orders
           SET payment_fee = 0
         WHERE payment_fee IS NULL
           AND source <> 'aggregator'
           AND (payment_method <> 'card' OR total <= 0)
        """
    )


def downgrade() -> None:
    op.drop_column("orders", "payment_fee")
    op.drop_column("orders", "aggregator_fee")
    op.drop_column("couriers", "payment_fee_fixed")
    op.drop_column("couriers", "payment_fee_percent")
    op.drop_column("couriers", "commission_fixed")
    op.drop_column("couriers", "commission_percent")
