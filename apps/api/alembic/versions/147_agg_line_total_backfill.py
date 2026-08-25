"""Backfill aggregator line totals that dropped their modifier price.

The GrubOps ingest took each line's `total_price` from GrubOps' own `totalPrice`
field with a fallback to `base_price`. A product whose price lives entirely on a
modifier — the "3 Pieces" charge on a Ferrero brownie, base 0 — has neither:
GrubOps omitted `totalPrice` and the fallback wrote 0, so the line read 0.00 on
the order summary and understated any report that sums `order_items.total_price`
(`pos_reports/sales.py`). The order's own subtotal was right — it is summed from
the GrubOps header, not from these lines — so nothing was mischarged; only the
per-line figure was wrong.

The ingest is fixed to write `unit_price * quantity` (the same figure the
register and the website write). This repairs the rows already taken the old way.

Scoped to `source = 'aggregator'` on purpose: a website or counter line may
legitimately carry `total_price` below `unit_price * quantity` when a line
discount applies (`pos_order_service` writes `net_of_discount`), and those must
not be touched. Aggregator lines carry no per-line discount, so `unit_price *
quantity` is the correct line total for every one of them.

Guarded the way content backfills are (canon rule 7): the write is conditional on
the value actually being wrong, so a re-run — or a restore from a later dump that
already has the fix — matches nothing and does nothing.

Revision ID: 147_agg_line_total_backfill
Revises: 146_foodics_order_id
Create Date: 2026-08-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "147_agg_line_total_backfill"
down_revision: Union[str, None] = "146_foodics_order_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            """
            UPDATE order_items AS oi
            SET total_price = oi.unit_price * oi.quantity
            FROM orders AS o
            WHERE o.id = oi.order_id
              AND o.source = 'aggregator'
              AND oi.total_price <> oi.unit_price * oi.quantity
            """
        )
    )
    print(f"147: corrected {result.rowcount} aggregator line total(s)")


def downgrade() -> None:
    # A value correction has no faithful inverse: the wrong figures were derived
    # from a GrubOps field that is not stored per line, so there is nothing to
    # restore them to. Left as a no-op, like the content backfills.
    pass
