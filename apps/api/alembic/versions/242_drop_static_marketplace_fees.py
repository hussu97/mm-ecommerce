"""Drop the static/modelled marketplace-fee configuration.

Marketplace commission and payment fees are now taken solely from each channel's
own scraped settlement statement (stamped onto `orders.aggregator_fee` /
`payment_fee`). The modelled configuration that predated that was already inert:
migration `169` NULLed the `couriers` rate columns, `order_fees.compute` stopped
reading them, and reconciliation stopped computing a modelled expected
commission. This removes the now-dead scaffolding.

Dropped:
  * `couriers` — the four modelled rate columns and the five fee-grammar flags.
    The rest of the table (identity, logistics, delivery promise) stays.
  * `courier_branch_rate` — the per-branch fee-override table. It had no reader
    at all (no service, no endpoint), so the whole table goes.
  * `aggregator_reconciliation.commission_expected` / `commission_variance` —
    always NULL since the modelled overcharge check was removed. The scraped
    `commission_actual` and `commission_rate_effective` stay.

Own-channel card-fee modelling (`payment_gateways.fee_percent` / `fee_fixed`) is
untouched: a website/counter order has no external statement, so its card fee is
genuinely ours to model.

Revision ID: 242_drop_static_marketplace_fees
Revises: 241_drop_legacy_slider
Create Date: 2026-09-13
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "242_drop_static_marketplace_fees"
down_revision: Union[str, None] = "241_drop_legacy_slider"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COURIER_RATE_COLS = (
    "commission_percent",
    "commission_fixed",
    "payment_fee_percent",
    "payment_fee_fixed",
)
_COURIER_FLAG_COLS = (
    "commission_vat_inclusive",
    "payment_fee_vat_inclusive",
    "commission_fixed_net_of_base",
    "payment_fee_cash_exempt",
    "commission_fixed_requires_member",
)


def upgrade() -> None:
    # The per-branch fee-override table has no reader; drop it whole.
    op.drop_index("ix_courier_branch_rate_branch_id", table_name="courier_branch_rate")
    op.drop_index("ix_courier_branch_rate_courier_id", table_name="courier_branch_rate")
    op.drop_table("courier_branch_rate")

    # The modelled rate columns and their grammar flags on `couriers`.
    for col in _COURIER_RATE_COLS + _COURIER_FLAG_COLS:
        op.drop_column("couriers", col)

    # The always-NULL modelled reconciliation columns.
    op.drop_column("aggregator_reconciliation", "commission_expected")
    op.drop_column("aggregator_reconciliation", "commission_variance")


def downgrade() -> None:
    # Re-add the reconciliation columns (nullable; they were always NULL).
    op.add_column(
        "aggregator_reconciliation",
        sa.Column("commission_variance", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "aggregator_reconciliation",
        sa.Column("commission_expected", sa.Numeric(12, 2), nullable=True),
    )

    # Re-add the couriers rate columns (nullable) and grammar flags (NOT NULL,
    # default false — the plain reading every historical row was written under).
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
    for col in _COURIER_FLAG_COLS:
        op.add_column(
            "couriers",
            sa.Column(
                col, sa.Boolean(), nullable=False, server_default=sa.text("false")
            ),
        )

    # Re-create the per-branch fee-override table.
    op.create_table(
        "courier_branch_rate",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("courier_id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column("commission_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("commission_fixed", sa.Numeric(10, 2), nullable=True),
        sa.Column("payment_fee_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("payment_fee_fixed", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["courier_id"], ["couriers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("courier_id", "branch_id", name="uq_courier_branch_rate"),
    )
    op.create_index(
        "ix_courier_branch_rate_courier_id", "courier_branch_rate", ["courier_id"]
    )
    op.create_index(
        "ix_courier_branch_rate_branch_id", "courier_branch_rate", ["branch_id"]
    )
