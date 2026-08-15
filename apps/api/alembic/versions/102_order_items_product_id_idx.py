"""Index the join every product report walks.

`order_items.product_id` is the foreign key that answers "what sold": product
performance, best-sellers, and any export that groups sales by product all join
through it, and every one of them was a sequential scan over the largest table
in the schema. The FK itself creates no index — Postgres only does that for the
referenced side.

Only this one. The other unindexed FKs on `orders` and `order_items`
(`creator_id`, `closer_id`, `driver_id`, `table_id`, `device_id`,
`voided_by_id`, `void_reason_id`) are audit columns: written once, read when a
person asks about one specific order, and never the driving side of a hot
query. Seven more indexes would tax every order write to speed up lookups
nobody runs; any of them can be added in its own migration the day a real
query wants it.

The model deliberately does not carry `index=True` for this: the reporting
index is an operational fact about the database, added here, not a property
of the mapping.

Revision ID: 102_order_items_product_id_idx
Revises: 101_updated_at_on_mutables
Create Date: 2026-08-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "102_order_items_product_id_idx"
down_revision: Union[str, None] = "101_updated_at_on_mutables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_order_items_product_id", "order_items", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_order_items_product_id", table_name="order_items")
