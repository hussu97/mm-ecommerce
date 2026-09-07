"""Record whether an order is holding stock it drew from the shelf.

A cancellation returns an order's stock through `order_lifecycle._move_stock`.
That was ungated, so an aggregator order promoted OUTSIDE the sales window — filed
for reconciliation linkage only, with no stock drawn (`draw_stock=False`) — would,
if later cancelled, RESTOCK inventory it never took, quietly inflating the shelf.

`stock_drawn` is the fact the restore now checks: the draw paths (website checkout,
the aggregator promote and the GrubOps ingest) set it true when they take stock,
and `_move_stock` returns early on a restore when it is false.

Backfill: an existing order is currently holding drawn stock when it drew at
creation and has not since given it back — i.e. a website or aggregator order that
is not cancelled and has at least one line mapped to a stock-tracked product. A
cancelled order already returned its stock (so it holds none → stays false), and a
counter sale never draws here (recipe ingredients deplete at close). Conservative
by design: it marks true only where stock was provably taken and is still held, so
a later cancellation returns exactly that and no more.

Guarded and idempotent: the column defaults false and the backfill matches only the
rows that already consumed stock, so a re-run (or a run over a restored dump) sets
the same rows and nothing else.

Revision ID: 202_order_stock_drawn
Revises: 201_report_input_drop_production
Create Date: 2026-09-07
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "203_order_stock_drawn"
down_revision: Union[str, None] = "202_till_open_uniqueness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "stock_drawn",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Mark the orders that drew stock at creation and are still holding it.
    op.execute(
        """
        UPDATE orders o
        SET stock_drawn = true
        WHERE o.source IN ('online', 'aggregator')
          AND o.status <> 'cancelled'
          AND EXISTS (
              SELECT 1
              FROM order_items oi
              JOIN products p ON p.id = oi.product_id
              WHERE oi.order_id = o.id
                AND p.is_stock_product = true
          )
        """
    )


def downgrade() -> None:
    op.drop_column("orders", "stock_drawn")
