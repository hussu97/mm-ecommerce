"""Put noon's payment and cancellation fees on the VAT-INCLUSIVE base too.

Migration 251 grossed noon's commission up by 5% VAT so it matched the other
channels and the `orders.*_fee` contract, but noon's OTHER per-order fees —
`payment_fee` and `cancellation_fee` — were left VAT-EXCLUSIVE. So a settled noon
order carried those two fees ~5% light, `OrderEconomics.net` overstated its net,
and the fee rate on the order (and the dashboard) read low against the statement
and the actual payout. The code fix grosses them up at ingest
(`noon_provider._fee_incl_vat`); this repairs the rows already stored ex-VAT.

Guarded like 251, and safe on a re-run or a restored dump: each pass matches only
rows still holding the exact ex-VAT value, keyed off the stored raw statement row
(`aggregator_order.raw`), so once corrected it matches nothing. Rows whose raw
carries no per-fee value (OMS-only, pre-settlement) are left alone — their fee is
the pre-settlement estimate, not the settled figure this repairs.

Revision ID: 278_noon_fee_vat_incl
Revises: 277_cater_kg_label_servings
Create Date: 2026-09-22
"""

from typing import Sequence, Union

from alembic import op

revision: str = "278_noon_fee_vat_incl"
down_revision: Union[str, None] = "277_cater_kg_label_servings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The raw value as noon reported it (either spelling), absolute, rounded — the
#: still-ex-VAT figure a stored fee must still equal to be grossed exactly once.
def _raw(field_snake: str, field_camel: str) -> str:
    return (
        f"round(abs(coalesce("
        f"nullif(ao.raw->>'{field_snake}','')::numeric, "
        f"nullif(ao.raw->>'{field_camel}','')::numeric)), 2)"
    )


def _gross_aggregator_fee(column: str, snake: str, camel: str) -> None:
    op.execute(
        f"""
        UPDATE aggregator_order ao
        SET {column} = round(ao.{column} * 1.05, 2)
        WHERE ao.channel = 'noon'
          AND ao.{column} IS NOT NULL
          AND ao.{column} <> 0
          AND ao.raw IS NOT NULL
          AND round(ao.{column}, 2) = {_raw(snake, camel)}
        """
    )


def _restamp_order_fee(column: str) -> None:
    # Re-derive the MM order fee from the corrected aggregator order, only where
    # the order fee grossed up by 5% still equals it — i.e. the order still holds
    # the ex-VAT figure promotion stamped, never a hand-edited value.
    op.execute(
        f"""
        UPDATE orders o
        SET {column} = ao.{column}
        FROM aggregator_order ao
        WHERE ao.mm_order_id = o.id
          AND ao.channel = 'noon'
          AND o.{column} IS NOT NULL
          AND o.{column} <> 0
          AND ao.{column} IS NOT NULL
          AND round(o.{column} * 1.05, 2) = ao.{column}
        """
    )


def upgrade() -> None:
    # Pass 1 — the source of truth: gross the aggregator order's fees.
    _gross_aggregator_fee("payment_fee", "payment_fee", "paymentFee")
    _gross_aggregator_fee("cancellation_fee", "cancellation_fee", "cancellationFee")
    # Pass 2 — re-stamp the MM order fees `OrderEconomics.net` subtracts.
    _restamp_order_fee("payment_fee")
    _restamp_order_fee("cancellation_fee")


def downgrade() -> None:
    # One-way correction, like 251: re-dividing back to ex-VAT would re-introduce
    # the mixed-base bug (noon ex-VAT against the other channels' inclusive fees).
    pass
