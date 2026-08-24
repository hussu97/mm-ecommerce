"""A seven-digit number a driver can read back.

Our order numbers are `MM-20260805-007`: fifteen characters, three of them
punctuation, and the part that distinguishes one order from the next is at the
far end. That is a good database key and a poor thing to say down a phone. noon
Send asked for something shorter so their riders can quote it, and the same
argument applies to the notes Lalamove shows its drivers.

Unique across the whole table rather than per courier. Both couriers only need
uniqueness within their own records, but one index satisfies both, survives an
order that falls back from noon Send to Lalamove halfway through a dispatch (the
reference stays; only the provider changes), and means a support call opening
with seven digits identifies exactly one order without anyone having to ask
which courier carried it.

Nullable, and deliberately not backfilled. A reference is drawn at dispatch, so
a third-party zone never spends one, and the rows that already exist describe
bookings whose driver notes were printed long ago — giving them a number now
would invent a reference nobody was ever told. `noon_send_service._delivery_for`
still matches an incoming `order_reference` against the order number as well, so
tasks created before this keep resolving.

Revision ID: 073_courier_reference
Revises: 072_order_status_undelivered
Create Date: 2026-08-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "073_courier_reference"
down_revision: Union[str, None] = "072_order_status_undelivered"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_deliveries",
        sa.Column("courier_reference", sa.String(length=7), nullable=True),
    )
    op.create_index(
        "ix_order_deliveries_courier_reference",
        "order_deliveries",
        ["courier_reference"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_order_deliveries_courier_reference", table_name="order_deliveries"
    )
    op.drop_column("order_deliveries", "courier_reference")
