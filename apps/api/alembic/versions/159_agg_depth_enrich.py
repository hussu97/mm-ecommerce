"""Enrich aggregator sales/finance for modifiers, customer, statement links.

Additive only. Sales rows gain customer + status timeline stamps and structured
item modifiers (JSONB). Statement lines gain an optional MM order link and a
grain flag so order-level fee lines can join promoted orders while summary-only
channels stay honest. Statement headers gain an optional external outlet id
(Talabat per-branch detailed workbooks).

Revision ID: 159_agg_depth_enrich
Revises: 158_merge_grubops_item_map
Create Date: 2026-08-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "159_agg_depth_enrich"
down_revision: Union[str, None] = "158_merge_grubops_item_map"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "aggregator_order",
        sa.Column("customer_name", sa.String(150), nullable=True),
    )
    op.add_column(
        "aggregator_order",
        sa.Column("customer_phone", sa.String(30), nullable=True),
    )
    op.add_column(
        "aggregator_order",
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "aggregator_order",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "aggregator_order",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column(
        "aggregator_order_item",
        sa.Column(
            "modifiers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    op.add_column(
        "aggregator_statement",
        sa.Column("external_outlet_id", sa.String(64), nullable=True),
    )

    op.add_column(
        "aggregator_statement_line",
        sa.Column(
            "mm_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "aggregator_statement_line",
        sa.Column(
            "grain",
            sa.String(16),
            nullable=False,
            server_default="order",
        ),
    )
    op.create_check_constraint(
        "ck_aggregator_statement_line_grain",
        "aggregator_statement_line",
        "grain IN ('order', 'summary')",
    )
    op.create_index(
        "ix_aggregator_statement_line_mm_order",
        "aggregator_statement_line",
        ["mm_order_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_aggregator_statement_line_mm_order",
        table_name="aggregator_statement_line",
    )
    op.drop_constraint(
        "ck_aggregator_statement_line_grain",
        "aggregator_statement_line",
        type_="check",
    )
    op.drop_column("aggregator_statement_line", "grain")
    op.drop_column("aggregator_statement_line", "mm_order_id")
    op.drop_column("aggregator_statement", "external_outlet_id")
    op.drop_column("aggregator_order_item", "modifiers")
    op.drop_column("aggregator_order", "cancelled_at")
    op.drop_column("aggregator_order", "delivered_at")
    op.drop_column("aggregator_order", "accepted_at")
    op.drop_column("aggregator_order", "customer_phone")
    op.drop_column("aggregator_order", "customer_name")
