"""Put noon's aggregator commission on the same VAT-INCLUSIVE base as the others.

noon reports its commission VAT-EXCLUSIVE (`fees_exc_vat`), so promotion stamped a
VAT-exclusive `aggregator_fee` onto the MM order and the reconciler stored a
VAT-exclusive rate — while Keeta, Careem and Talabat all carry `commission_amount`
VAT-inclusive, and `OrderEconomics.net` subtracts it verbatim as the real cost.
noon's net was therefore overstated by the 5% commission VAT, and its
reconciliation rate read ex-VAT against the others' inclusive rate. The code fix
grosses noon commission up by 5% at ingest (`noon_provider._commission_from`); this
repairs the rows already stored the old way, in three joined passes.

Guarded so it cannot fight anything and is safe on a re-run or a restored dump —
every pass matches only rows still holding the exact ex-VAT value (the grossed-up
figure equals the corrected commission) and, once corrected, matches nothing:

  1. `aggregator_order.commission_amount` (the source of truth): grossed up by 5%,
     but only for noon rows still equal to that order's settled ex-VAT commission
     statement line (so a re-run over an already-inclusive value finds nothing).
  2. `orders.aggregator_fee` (what net is computed from): re-derived from the
     corrected aggregator order, only where the order's fee grossed up by 5% still
     equals the new commission — i.e. the fee is still the ex-VAT figure promotion
     stamped, not a hand-edited value.
  3. `aggregator_reconciliation.commission_actual` + `commission_rate_effective`:
     the reconciler keeps its own copies, so refresh them from the corrected value
     and recompute the rate over the stored base, under the same still-ex-VAT guard.

Revision ID: 251_noon_commission_incl
Revises: 250_product_consumes_stock
Create Date: 2026-09-17
"""

from typing import Sequence, Union

from alembic import op

revision: str = "251_noon_commission_incl"
down_revision: Union[str, None] = "250_product_consumes_stock"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Pass 1 — the source of truth. Gross up only noon commissions that still equal
    # the order's settled ex-VAT commission statement line; once ×1.05 they no
    # longer match, so a re-run or a restore of an older dump touches nothing.
    op.execute(
        """
        UPDATE aggregator_order ao
        SET commission_amount = round(ao.commission_amount * 1.05, 2)
        FROM (
            SELECT external_order_id, sum(abs(amount)) AS stmt_comm
            FROM aggregator_statement_line
            WHERE channel = 'noon'
              AND fee_category = 'commission'
              AND coalesce(lower(line_type), '') <> 'vat'
              AND fee_category NOT LIKE '%vat%'
            GROUP BY external_order_id
        ) sc
        WHERE ao.channel = 'noon'
          AND ao.commission_amount IS NOT NULL
          AND sc.external_order_id = ao.external_order_id
          AND round(ao.commission_amount, 2) = round(sc.stmt_comm, 2)
        """
    )

    # Pass 2 — re-derive the MM order fee (what OrderEconomics.net subtracts) from
    # the now-corrected aggregator order. Guard: the order fee grossed up by 5%
    # still equals the corrected commission — i.e. the fee is still the ex-VAT
    # figure promotion stamped, never a value someone edited afterwards.
    op.execute(
        """
        UPDATE orders o
        SET aggregator_fee = ao.commission_amount
        FROM aggregator_order ao
        WHERE ao.mm_order_id = o.id
          AND ao.channel = 'noon'
          AND o.aggregator_fee IS NOT NULL
          AND round(o.aggregator_fee * 1.05, 2) = ao.commission_amount
        """
    )

    # Pass 3 — the reconciler stores its own copies, so refresh them from the
    # corrected aggregator order and recompute the effective rate over the stored
    # base (total_agg, falling back to total_mm), under the same still-ex-VAT guard.
    op.execute(
        """
        UPDATE aggregator_reconciliation r
        SET commission_actual = ao.commission_amount,
            commission_rate_effective = CASE
                WHEN coalesce(r.total_agg, r.total_mm) IS NOT NULL
                     AND coalesce(r.total_agg, r.total_mm) <> 0
                THEN round(ao.commission_amount / coalesce(r.total_agg, r.total_mm), 4)
                ELSE r.commission_rate_effective
            END
        FROM aggregator_order ao
        WHERE ao.channel = 'noon'
          AND r.channel = 'noon'
          AND r.external_order_id = ao.external_order_id
          AND r.commission_actual IS NOT NULL
          AND round(r.commission_actual * 1.05, 2) = ao.commission_amount
        """
    )


def downgrade() -> None:
    # One-way correction: re-dividing the figures back to VAT-exclusive would
    # re-introduce the mixed-base bug (noon ex-VAT against the other channels'
    # inclusive commission), so there is no faithful reversal.
    pass
