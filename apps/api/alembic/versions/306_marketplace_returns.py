"""Marketplace returns: the Talabat return PIN and whether the box came back.

`marketplace_returns`, one row per order a marketplace cancelled after its rider
had collected it (see `app.models.marketplace_return`). Holds the return PIN the
rider must be given, the marketplace's cancel owner/reason, and who took the box
back in on the register (and the restock movement that followed).

Revision ID: 306_marketplace_returns
Revises: 305_cancel_net_provisional
Create Date: 2026-09-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "306_marketplace_returns"
down_revision: Union[str, None] = "305_cancel_net_provisional"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "marketplace_returns",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("external_order_id", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pin_pending",
        ),
        sa.Column("return_pin", sa.String(16), nullable=True),
        sa.Column("pin_source", sa.String(10), nullable=True),
        sa.Column("pin_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_owner", sa.String(40), nullable=True),
        sa.Column("cancel_reason", sa.String(120), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("received_by_label", sa.String(255), nullable=True),
        sa.Column(
            "restock_transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inventory_transactions.id", ondelete="SET NULL"),
            nullable=True,
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
        sa.CheckConstraint(
            "status IN ('pin_pending', 'awaiting_return', 'received', 'not_returning')",
            name="ck_marketplace_returns_status",
        ),
        sa.CheckConstraint(
            "pin_source IS NULL OR pin_source IN ('trigger', 'scrape')",
            name="ck_marketplace_returns_pin_source",
        ),
    )
    op.create_index(
        "ix_marketplace_returns_channel_external",
        "marketplace_returns",
        ["channel", "external_order_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_marketplace_returns_channel_external", table_name="marketplace_returns"
    )
    op.drop_table("marketplace_returns")
