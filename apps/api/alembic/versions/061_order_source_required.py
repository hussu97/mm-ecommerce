"""Every order names the channel it came from.

`orders.source` arrived with the POS and was only ever set by the counter, so
every storefront order written before this carries null. That left a filter
nobody can read: "the website" had to mean *everything that is not the counter*,
because null is a storefront order that simply predates the column.

A nullable column with one meaning for null is a column with a footnote. So the
old rows are backfilled — they are storefront orders, that is not a guess — and
the column becomes `NOT NULL`, which means a channel filter is now `source =
'online'` and reads as what it is.

No server default, deliberately. Both writers know their channel at the moment
they write, and an INSERT that forgets should fail loudly rather than quietly
land in whichever bucket the default happened to name.

Paired with the fix that makes the storefront stamp `source` at creation rather
than when the order reaches a register — without it, an order in a zone with no
branch would be written with no channel and violate this constraint.

Revision ID: 061_order_source_required
Revises: 060_device_push_tokens
Create Date: 2026-08-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "061_order_source_required"
down_revision: Union[str, None] = "060_device_push_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Null has only ever meant one thing here. A counter order gets `cashier`
    # from `open_order` and always has, so anything without a source came
    # through the storefront.
    result = conn.execute(
        sa.text("UPDATE orders SET source = 'online' WHERE source IS NULL")
    )
    print(f"  backfilled {result.rowcount} order(s) as 'online'")

    op.alter_column("orders", "source", existing_type=sa.String(20), nullable=False)


def downgrade() -> None:
    # The backfill is not undone: those rows really are storefront orders, and
    # putting the nulls back would restore an ambiguity rather than a fact.
    op.alter_column("orders", "source", existing_type=sa.String(20), nullable=True)
