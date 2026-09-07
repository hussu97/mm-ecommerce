"""Add orders.inventory_revision, the sales-consumption counter (F-INV-10).

The inventory source event's idempotency key was hardcoded to revision 1, so a
post-close billable-line edit could never be re-consumed — the poster returned
the stale first event. This adds the per-order counter the key now derives from:
a billable-line edit bumps it and `source_event_service.reconsume_order` reverses
the prior movement and posts a fresh consumption at the new revision.

Additive, NOT NULL with a server default of 1: every existing order is at its
first (and only) revision, which is exactly what the old hardcoded 1 meant.

Revision ID: 213_order_inventory_revision
Revises: 212_redirect_product_source
Create Date: 2026-09-07
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "213_order_inventory_revision"
down_revision: Union[str, None] = "212_redirect_product_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "inventory_revision",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("orders", "inventory_revision")
