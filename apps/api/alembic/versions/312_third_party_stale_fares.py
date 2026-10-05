"""Clear two cancelled Lalamove fares left on third-party deliveries.

MM-20260821-001 and MM-20260912-001 were booked on Lalamove, the booking was
rejected / cancelled, and the order was moved to a third party by hand. The
move kept the Lalamove fare on `cost_total`, so the P&L booked 59.00 and 72.00
of courier cost Lalamove never charged. A third party's cost is now only what a
person enters after delivery (`OrderDelivery.courier_cost`), and the move clears
the old fare (`fulfilment_reassignment`); this clears the two already there.

Guarded to the exact rows and values it means to replace: once someone enters a
real cost on either order, it matches nothing — including on a database restored
from an older dump. The downgrade puts the fares back as they were.

Revision ID: 312_third_party_stale_fares
Revises: 311_rebuild_customer_cache
Create Date: 2026-10-05
"""

from typing import Sequence, Union

from alembic import op

revision: str = "312_third_party_stale_fares"
down_revision: Union[str, None] = "311_rebuild_customer_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ROWS = (
    ("MM-20260821-001", "59.00", "REJECTED"),
    ("MM-20260912-001", "72.00", "CANCELED"),
)


def upgrade() -> None:
    for order_number, fare, lalamove_status in _ROWS:
        op.execute(
            f"""
            UPDATE order_deliveries AS d
               SET cost_total = NULL, price_breakdown = NULL
              FROM orders AS o
             WHERE o.id = d.order_id
               AND o.order_number = '{order_number}'
               AND d.provider = 'third_party'
               AND d.original_provider = 'lalamove'
               AND d.courier_previous_status = '{lalamove_status}'
               AND d.cost_total = {fare}
               AND d.quoted_cost = {fare}
            """
        )


def downgrade() -> None:
    for order_number, fare, lalamove_status in _ROWS:
        op.execute(
            f"""
            UPDATE order_deliveries AS d
               SET cost_total = {fare}
              FROM orders AS o
             WHERE o.id = d.order_id
               AND o.order_number = '{order_number}'
               AND d.provider = 'third_party'
               AND d.courier_previous_status = '{lalamove_status}'
               AND d.cost_total IS NULL
               AND d.quoted_cost = {fare}
            """
        )
