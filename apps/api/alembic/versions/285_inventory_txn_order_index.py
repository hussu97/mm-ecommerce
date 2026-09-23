"""Index `inventory_transactions.order_id` for the P&L's per-order COGS read.

The profit & loss columns on the orders list read each order's cost of goods
with a correlated lookup by `order_id` — up to two thousand per page — and the
column had no index. Partial, because most movements (purchases, counts,
transfers, production) carry no order.

Index-only: no row is touched, so the closed-transaction immutability trigger
is not involved.

Revision ID: 285_inventory_txn_order_index
Revises: 284_counter_local_first
"""

from typing import Sequence, Union

from alembic import op

revision: str = "285_inventory_txn_order_index"
down_revision: Union[str, None] = "284_counter_local_first"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inventory_transactions_order_id "
        "ON inventory_transactions (order_id) WHERE order_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_inventory_transactions_order_id")
