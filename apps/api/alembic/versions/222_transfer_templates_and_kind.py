"""Transfer templates, a transfer/return kind, and a per-line variance reason.

Phase 3 of the POS WMS work:

1. ``inventory_transfer_templates`` (+ items) — a saved item list a branch
   transfers from, so a cashier picks a template and fills quantities. The sending
   branch's analogue of a shift-report template.
2. ``transfer_orders.kind`` — ``transfer`` (branch → branch) or ``return`` (branch
   → its return branch). Returns reuse the whole transfer machinery, so they share
   the table. Existing rows are all transfers (server default).
3. ``transfer_order_items.variance_reason`` — why a return line is going back, or
   the receiver's note when what arrived differs from what was sent.

Revision ID: 222_transfer_templates_and_kind
Revises: 221_report_comments
Create Date: 2026-09-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "222_transfer_templates_and_kind"
down_revision: Union[str, None] = "221_report_comments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inventory_transfer_templates",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "source_branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "destination_branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "display_order", sa.Numeric(6, 0), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "inventory_transfer_template_items",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "template_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inventory_transfer_templates.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inventory_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "display_order", sa.Numeric(6, 0), nullable=False, server_default="0"
        ),
    )

    op.add_column(
        "transfer_orders",
        sa.Column(
            "kind",
            sa.String(20),
            nullable=False,
            server_default="transfer",
        ),
    )
    op.create_index("ix_transfer_orders_kind", "transfer_orders", ["kind"])
    op.create_check_constraint(
        "ck_transfer_orders_kind",
        "transfer_orders",
        "kind IN ('transfer', 'return')",
    )

    op.add_column(
        "transfer_order_items",
        sa.Column("variance_reason", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transfer_order_items", "variance_reason")
    op.drop_constraint("ck_transfer_orders_kind", "transfer_orders", type_="check")
    op.drop_index("ix_transfer_orders_kind", table_name="transfer_orders")
    op.drop_column("transfer_orders", "kind")
    op.drop_table("inventory_transfer_template_items")
    op.drop_table("inventory_transfer_templates")
