"""Gross up the noon fees migration 278 could not reach: the OMS-shaped rows.

278 grossed noon's payment/cancellation fees to VAT-inclusive, but guarded off an
RMS-shaped `raw` (a `payment_fee` key). Two sets of rows store their `raw` in the
OMS shape instead (an `orderPostpaidFee` key) and so were missed:

  * ~23 pre-settlement OMS orders, whose `payment_fee` is `abs(orderPostpaidFee)`
    — noon reports that VAT-EXCLUSIVE too (confirmed with the shop);
  * ~53 SETTLED orders whose fee came from the statement (RMS) but whose stored
    `raw` is the OMS shape, so 278's key guard skipped them.

The code fix grosses the OMS `orderPostpaidFee` at ingest too
(`noon_provider._order_from_oms`); this repairs the rows already stored ex-VAT.

Idempotency: the payment-fee pass excludes rows already VAT-inclusive relative to
`orderPostpaidFee`, so the OMS subset and any future grossed OMS order are never
re-grossed. The settled-OMS-raw subset (orderPostpaidFee = 0, fee from the
statement) has no stored ex-VAT reference to compare against — 278 already
consumed the only one for the RMS-shaped rows — so those rely on alembic's
single application, like any ordinary data migration. The order re-stamp (pass 3)
is fully value-guarded. Validated on prod in a rolled-back transaction.

Revision ID: 279_noon_oms_fee_vat_incl
Revises: 278_noon_fee_vat_incl
Create Date: 2026-09-22
"""

from typing import Sequence, Union

from alembic import op

revision: str = "279_noon_oms_fee_vat_incl"
down_revision: Union[str, None] = "278_noon_fee_vat_incl"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Pass 1 — payment fee on the OMS-shaped rows. The final clause skips a row
    # already grossed relative to its raw orderPostpaidFee, so the OMS subset and
    # any future ingest are idempotent; the settled-OMS-raw subset (raw fee 0) is
    # matched here and relies on single application.
    op.execute(
        """
        UPDATE aggregator_order ao
        SET payment_fee = round(ao.payment_fee * 1.05, 2)
        WHERE ao.channel = 'noon'
          AND ao.payment_fee IS NOT NULL
          AND ao.payment_fee <> 0
          AND ao.raw ? 'orderPostpaidFee'
          AND NOT (ao.raw ? 'payment_fee')
          AND round(ao.payment_fee, 2)
              <> round(abs(coalesce(nullif(ao.raw->>'orderPostpaidFee', '')::numeric, 0)) * 1.05, 2)
        """
    )

    # Pass 2 — cancellation fee on the OMS-shaped rows (noon's OMS feed carries no
    # cancellation key, so these are settled fees stored beside an OMS raw). No
    # stored ex-VAT reference; single application.
    op.execute(
        """
        UPDATE aggregator_order ao
        SET cancellation_fee = round(ao.cancellation_fee * 1.05, 2)
        WHERE ao.channel = 'noon'
          AND ao.cancellation_fee IS NOT NULL
          AND ao.cancellation_fee <> 0
          AND ao.raw ? 'orderPostpaidFee'
          AND NOT (ao.raw ? 'cancellation_fee')
        """
    )

    # Pass 3 — re-stamp the MM order fees OrderEconomics.net subtracts, from the
    # now-corrected aggregator order. Value-guarded: only orders still holding the
    # ex-VAT figure (× 1.05 equals the corrected aggregator fee) are touched.
    for column in ("payment_fee", "cancellation_fee"):
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


def downgrade() -> None:
    # One-way correction, like 251 and 278.
    pass
