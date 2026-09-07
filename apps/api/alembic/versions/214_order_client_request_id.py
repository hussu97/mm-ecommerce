"""Order idempotency key: orders.client_request_id + partial unique index.

One additive, nullable column and the partial unique index that gives it teeth
(F-WEB-5). The storefront mints a `client_request_id` per checkout attempt and
replays it when a `POST /orders` times out; the API stores it, returns the
existing order for a repeat, and — this index — turns two near-simultaneous
creates of the same id into a caught conflict rather than a duplicate order.

Nullable with no default and no backfill: it describes only storefront orders
placed from here on. Every counter sale, every aggregator order, and every
storefront client from before this shipped carries NULL, and the index is
partial (`WHERE client_request_id IS NOT NULL`) so any number of those NULLs
coexist while a non-null value is unique across the table.

Revision ID: 214_order_client_request_id
Revises: 209_auth_session_revocation
Create Date: 2026-09-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "214_order_client_request_id"
down_revision: Union[str, None] = "213_order_inventory_revision"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("client_request_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "uq_orders_client_request_id",
        "orders",
        ["client_request_id"],
        unique=True,
        postgresql_where=sa.text("client_request_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_orders_client_request_id", table_name="orders")
    op.drop_column("orders", "client_request_id")
