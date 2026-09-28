"""Couriers: how close to the promised time a rider may collect before the
order is late.

`couriers.delay_window_minutes`. Once a website order is on the way, the
customer keeps the estimate checkout gave them — unless the rider collected it
within this many minutes of that time (or after it), in which case the order is
running late and the estimate is rebuilt from the pickup. Null means the old
behaviour: every pickup rebuilds the estimate.

Seeded as the shop asked on 2026-09-28: 15 minutes for the bike couriers (Slider
bike, noon Send), 30 for the car ones (Lalamove, Slider car). Only where the
column is still null, so it never fights the Estimates screen.

Revision ID: 302_courier_delay_window
Revises: 301_supplier_trade_license
Create Date: 2026-09-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "302_courier_delay_window"
down_revision: Union[str, None] = "301_supplier_trade_license"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Spelled out rather than read from anywhere, so this revision keeps meaning
#: what it meant.
_SEED = {"slider_bike": 15, "noon_send": 15, "lalamove": 30, "slider_car": 30}


def upgrade() -> None:
    op.add_column(
        "couriers", sa.Column("delay_window_minutes", sa.Integer(), nullable=True)
    )
    op.create_check_constraint(
        "ck_courier_delay_window_minutes",
        "couriers",
        "delay_window_minutes IS NULL OR delay_window_minutes BETWEEN 0 AND 600",
    )
    # Literals rather than bind parameters: the deploy runs this under asyncpg,
    # which cannot type an untyped parameter compared against a column.
    for code, minutes in _SEED.items():
        op.execute(
            f"UPDATE couriers SET delay_window_minutes = {int(minutes)} "
            f"WHERE code = '{code}' AND delay_window_minutes IS NULL"
        )


def downgrade() -> None:
    op.drop_constraint("ck_courier_delay_window_minutes", "couriers", type_="check")
    op.drop_column("couriers", "delay_window_minutes")
