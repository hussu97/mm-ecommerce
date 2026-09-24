"""Widen `email_logs.order_number` and give non-order emails their own `reference`.

`order_number` was VARCHAR(30), set before migration 284 widened
`orders.order_number` to 40 for local-first counter sales
(`POS-{ref[:10]}-{date}-{display_number}`). The counter pricing-mismatch alert
logs that number, so its log row would fail to insert. Widened to 64 —
metadata-only in Postgres, no rewrite, and the index survives.

The inventory-report email was already hitting it: it logged the report's UUID
(36 chars) as the "order number", and three of its log rows were lost on
2026-09-24. The transfer and purchase-order variance emails logged their
references there too. The admin renders `order_number` as a link to
`/orders/{n}`, so those rows were broken order links even when they fitted. They
now log to `reference`, and the existing rows move across.

Revision ID: 286_email_log_reference
Revises: 285_inventory_txn_order_index
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "286_email_log_reference"
down_revision: Union[str, None] = "285_inventory_txn_order_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Templates whose "order number" was never an order's.
_NON_ORDER_TEMPLATES = (
    "'inventory_report_submitted', "
    "'transfer_sending_variance', "
    "'purchase_order_receiving_variance'"
)


def upgrade() -> None:
    op.alter_column(
        "email_logs",
        "order_number",
        existing_type=sa.String(30),
        type_=sa.String(64),
        existing_nullable=True,
    )
    op.add_column("email_logs", sa.Column("reference", sa.String(64), nullable=True))
    op.execute(
        "UPDATE email_logs SET reference = order_number, order_number = NULL "
        f"WHERE template IN ({_NON_ORDER_TEMPLATES}) AND order_number IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE email_logs SET order_number = left(reference, 30) "
        f"WHERE template IN ({_NON_ORDER_TEMPLATES}) AND reference IS NOT NULL"
    )
    op.drop_column("email_logs", "reference")
    op.execute(
        "UPDATE email_logs SET order_number = left(order_number, 30) "
        "WHERE length(order_number) > 30"
    )
    op.alter_column(
        "email_logs",
        "order_number",
        existing_type=sa.String(64),
        type_=sa.String(30),
        existing_nullable=True,
    )
