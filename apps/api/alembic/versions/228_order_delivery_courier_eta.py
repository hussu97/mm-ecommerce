"""Persist the live courier ETA on the order delivery.

`order_deliveries.courier_eta_at` holds the absolute estimated-delivery time a
courier we book (Lalamove / noon Send / Slider) reports for a run once a rider
is on the way. It is the truest input to the out-for-delivery estimate, shown
ahead of any pickup-plus-duration calculation. Nullable — most rows never have
one (third-party orders, and integrator orders before pickup).

Revision ID: 228_order_delivery_courier_eta
Revises: 227_lalamove_slider_fallback
Create Date: 2026-09-11
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "228_order_delivery_courier_eta"
down_revision: Union[str, None] = "227_lalamove_slider_fb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_deliveries",
        sa.Column("courier_eta_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("order_deliveries", "courier_eta_at")
