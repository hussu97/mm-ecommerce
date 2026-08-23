"""Give couriers a logo, add the aggregator channels as couriers, and split the
aggregator's customer-facing delivery fee off our books.

Three small, additive things that let the register and the admin *identify* who
is carrying an order at a glance, and stop an aggregator's delivery charge from
being counted as ours:

* ``couriers.logo_url`` — a public image per carrier, served from the same R2
  bucket product images live in (``.../couriers/{code}.png``). The URL is a
  convention, so a frontend can build it from a code alone; the column is the
  editable source of truth.
* ``couriers.is_aggregator`` — marks the five marketplace channels (Talabat,
  Keeta, Noon Food, Deliveroo, Careem) apart from the couriers we dispatch
  ourselves. They are couriers only in the sense of *who hands the bag to the
  customer*; MM books none of them, so nothing may offer them as a dispatch
  target. The couriers table is only ever read by ``code`` (never enumerated for
  target selection — that comes off the polygons), so the rows are inert there
  by construction; the flag makes the distinction explicit anyway.
* ``orders.aggregator_delivery_fee`` — the delivery charge the aggregator showed
  *the customer*. It is theirs, not ours: it is not part of ``totalPrice``
  (verified against live orders — ``total == net + tax``, delivery excluded) and
  it is not money MM collects. It is recorded for the receipt/print only and
  kept out of ``orders.delivery_fee`` (which feeds sales and economics) so it can
  never inflate a delivery-revenue report.

The five aggregator rows and the logo_url on the existing couriers are upserted
here (INSERT ... ON CONFLICT) so the seed is idempotent and safe to re-run.

Revision ID: 133_courier_logos_agg
Revises: 132_grubops_orders
Create Date: 2026-08-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "133_courier_logos_agg"
down_revision: Union[str, None] = "132_grubops_orders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Where the logos live — the same public bucket the storefront's product
#: images do. A migration cannot read app settings, and this is the production
#: media host for every environment that has real logos, so it is written plain.
_MEDIA = "https://media.meltingmomentscakes.com"


def _logo(code: str) -> str:
    return f"{_MEDIA}/couriers/{code}.png"


#: The couriers we dispatch ourselves — already rows in the table (088/slider),
#: here only to receive a logo_url. is_aggregator stays false.
_DISPATCH_CODES = ["lalamove", "noon_send", "slider", "third_party"]

#: (code, display name) for the marketplace channels. `code` matches the
#: normalisation in `courier_catalog.code_for_channel`; `noon_food` is kept
#: distinct from the `noon_send` courier deliberately — they are two different
#: "noon" businesses.
_AGGREGATORS = [
    ("talabat", "Talabat"),
    ("keeta", "Keeta"),
    ("noon_food", "Noon Food"),
    ("deliveroo", "Deliveroo"),
    ("careem", "Careem"),
]


def upgrade() -> None:
    op.add_column(
        "couriers", sa.Column("logo_url", sa.String(length=300), nullable=True)
    )
    op.add_column(
        "couriers",
        sa.Column(
            "is_aggregator",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "orders",
        sa.Column("aggregator_delivery_fee", sa.Numeric(10, 2), nullable=True),
    )
    # The short, driver-facing pickup code (Talabat "1445", Noon "5717", or the
    # GrubOps sequence where no aggregator short code exists). Printed on the
    # ticket in place of the long external id — the driver reads this, not the
    # 16-digit marketplace order id.
    op.add_column(
        "orders",
        sa.Column("aggregator_display_code", sa.String(length=32), nullable=True),
    )

    # Logo onto the couriers we already have.
    for code in _DISPATCH_CODES:
        op.execute(
            sa.text(
                "UPDATE couriers SET logo_url = :logo WHERE code = :code"
            ).bindparams(logo=_logo(code), code=code)
        )

    # The five marketplace channels, as couriers flagged is_aggregator. Upsert so
    # a re-run (or a row a later hand-seed added) simply refreshes name/logo.
    for code, name in _AGGREGATORS:
        op.execute(
            sa.text(
                "INSERT INTO couriers "
                "(code, name, is_aggregator, logo_url, supports_batching, "
                " unbatched_promise_kind, is_active) "
                "VALUES (:code, :name, true, :logo, false, 'next_day', true) "
                "ON CONFLICT (code) DO UPDATE SET "
                "name = EXCLUDED.name, is_aggregator = true, "
                "logo_url = EXCLUDED.logo_url"
            ).bindparams(code=code, name=name, logo=_logo(code))
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM couriers WHERE code IN "
            "('talabat','keeta','noon_food','deliveroo','careem')"
        )
    )
    op.drop_column("orders", "aggregator_display_code")
    op.drop_column("orders", "aggregator_delivery_fee")
    op.drop_column("couriers", "is_aggregator")
    op.drop_column("couriers", "logo_url")
