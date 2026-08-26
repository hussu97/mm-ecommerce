"""The tables that mirror the delivery aggregators' own ledgers into mm_ecommerce.

Five marketplaces sell this shop's food and each keeps its own record of every
order, the fees it took and the payout it sends. These tables hold that record
so the shop can reconcile it against its own orders — missing items, post-
delivery refunds, and the commission actually charged. Shapes and reasoning are
on the models in `app/models/aggregator.py`.

Schema only. Nothing is seeded here — branch↔outlet rows are discovered from
each portal during bootstrap, and no branch reference is hardcoded (the same
lesson migration 131 records: a migration that silently seeds nothing against a
database with different reference codes is worse than one that does not try).

Also adds `branches.timezone` — every branch is Asia/Dubai today, carried per
branch so a future shop in another city reads its trading hours in the right
zone.

Revision ID: 151_aggregator_ingestion
Revises: 150_agg_cancel_reason
Create Date: 2026-08-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "151_aggregator_ingestion"
down_revision: Union[str, None] = "150_agg_cancel_reason"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CHANNELS = "'careem', 'deliveroo', 'talabat', 'noon', 'keeta'"


def _id() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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


def upgrade() -> None:
    op.add_column(
        "branches",
        sa.Column(
            "timezone",
            sa.String(40),
            nullable=False,
            server_default="Asia/Dubai",
        ),
    )

    op.create_table(
        "aggregator_branch_map",
        _id(),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_outlet_id", sa.String(64), nullable=True),
        sa.Column("external_brand_id", sa.String(64), nullable=True),
        sa.Column("external_company_id", sa.String(64), nullable=True),
        sa.Column("channel_ref", sa.String(120), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        *_timestamps(),
        sa.UniqueConstraint("channel", "branch_id", name="uq_aggregator_branch_map"),
        sa.CheckConstraint(
            f"channel IN ({_CHANNELS})", name="ck_aggregator_branch_map_channel"
        ),
    )
    op.create_index(
        "ix_aggregator_branch_map_branch", "aggregator_branch_map", ["branch_id"]
    )

    op.create_table(
        "foodics_branch_map",
        _id(),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("foodics_branch_id", sa.String(64), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        *_timestamps(),
        sa.UniqueConstraint("branch_id", name="uq_foodics_branch_map_branch"),
    )

    op.create_table(
        "aggregator_session",
        _id(),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("account_ref", sa.String(64), nullable=False, server_default=""),
        sa.Column("cookies_encrypted", sa.Text(), nullable=True),
        sa.Column("tokens_encrypted", sa.Text(), nullable=True),
        sa.Column("header_profile_encrypted", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cookie_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="needs_bootstrap",
        ),
        sa.Column("last_bootstrap_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_warmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("channel", "account_ref", name="uq_aggregator_session"),
        sa.CheckConstraint(
            f"channel IN ({_CHANNELS})", name="ck_aggregator_session_channel"
        ),
        sa.CheckConstraint(
            "status IN ('live', 'needs_bootstrap', 'dead')",
            name="ck_aggregator_session_status",
        ),
    )

    op.create_table(
        "aggregator_sync_run",
        _id(),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("from_date", sa.String(10), nullable=True),
        sa.Column("to_date", sa.String(10), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="planned"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            f"channel IN ({_CHANNELS})", name="ck_aggregator_sync_run_channel"
        ),
        sa.CheckConstraint(
            "mode IN ('sales', 'finance', 'backfill')",
            name="ck_aggregator_sync_run_mode",
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'running', 'completed', 'failed', 'partial')",
            name="ck_aggregator_sync_run_status",
        ),
    )
    op.create_index(
        "ix_aggregator_sync_run_channel", "aggregator_sync_run", ["channel"]
    )

    op.create_table(
        "aggregator_order",
        _id(),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("external_order_id", sa.String(64), nullable=False),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("business_date", sa.String(10), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(40), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("gross_sales", sa.Numeric(12, 2), nullable=True),
        sa.Column("net_sales", sa.Numeric(12, 2), nullable=True),
        sa.Column("commission_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("payment_fee", sa.Numeric(12, 2), nullable=True),
        sa.Column("delivery_fee", sa.Numeric(12, 2), nullable=True),
        sa.Column("vat_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("cancellation_fee", sa.Numeric(12, 2), nullable=True),
        sa.Column("refund_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("net_payable", sa.Numeric(12, 2), nullable=True),
        sa.Column("statement_id", sa.String(64), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("channel", "external_order_id", name="uq_aggregator_order"),
        sa.CheckConstraint(
            f"channel IN ({_CHANNELS})", name="ck_aggregator_order_channel"
        ),
    )
    op.create_index("ix_aggregator_order_branch", "aggregator_order", ["branch_id"])
    op.create_index(
        "ix_aggregator_order_business_date",
        "aggregator_order",
        ["channel", "business_date"],
    )

    op.create_table(
        "aggregator_order_item",
        _id(),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("source_key", sa.String(120), nullable=False),
        sa.Column(
            "aggregator_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("aggregator_order.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("grain", sa.String(16), nullable=False),
        sa.Column("item_name", sa.String(255), nullable=True),
        sa.Column("category_name", sa.String(255), nullable=True),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=True),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("gross_sales", sa.Numeric(12, 2), nullable=True),
        sa.Column("net_sales", sa.Numeric(12, 2), nullable=True),
        sa.Column(
            "amount_is_known",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("modifiers_text", sa.Text(), nullable=True),
        sa.Column("business_date", sa.String(10), nullable=True),
        sa.Column("period_start", sa.String(10), nullable=True),
        sa.Column("period_end", sa.String(10), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("channel", "source_key", name="uq_aggregator_order_item"),
        sa.CheckConstraint(
            f"channel IN ({_CHANNELS})", name="ck_aggregator_order_item_channel"
        ),
        sa.CheckConstraint(
            "grain IN ('line', 'aggregate')", name="ck_aggregator_order_item_grain"
        ),
    )
    op.create_index(
        "ix_aggregator_order_item_order",
        "aggregator_order_item",
        ["aggregator_order_id"],
    )

    op.create_table(
        "aggregator_statement",
        _id(),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("statement_id", sa.String(64), nullable=False),
        sa.Column("period_start", sa.String(10), nullable=True),
        sa.Column("period_end", sa.String(10), nullable=True),
        sa.Column("payment_due_date", sa.String(10), nullable=True),
        sa.Column("gross_sales", sa.Numeric(12, 2), nullable=True),
        sa.Column("net_payable", sa.Numeric(12, 2), nullable=True),
        sa.Column("total_fees", sa.Numeric(12, 2), nullable=True),
        sa.Column("total_vat", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("channel", "statement_id", name="uq_aggregator_statement"),
        sa.CheckConstraint(
            f"channel IN ({_CHANNELS})", name="ck_aggregator_statement_channel"
        ),
    )

    op.create_table(
        "aggregator_statement_line",
        _id(),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("source_key", sa.String(120), nullable=False),
        sa.Column("statement_id", sa.String(64), nullable=True),
        sa.Column("transfer_id", sa.String(64), nullable=True),
        sa.Column("external_order_id", sa.String(64), nullable=True),
        sa.Column("line_date", sa.String(10), nullable=True),
        sa.Column("line_type", sa.String(60), nullable=True),
        sa.Column("fee_category", sa.String(60), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "channel", "source_key", name="uq_aggregator_statement_line"
        ),
        sa.CheckConstraint(
            f"channel IN ({_CHANNELS})", name="ck_aggregator_statement_line_channel"
        ),
    )
    op.create_index(
        "ix_aggregator_statement_line_statement",
        "aggregator_statement_line",
        ["statement_id"],
    )
    op.create_index(
        "ix_aggregator_statement_line_order",
        "aggregator_statement_line",
        ["external_order_id"],
    )

    op.create_table(
        "aggregator_payout",
        _id(),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("transfer_id", sa.String(64), nullable=False),
        sa.Column("statement_id", sa.String(64), nullable=True),
        sa.Column("transfer_date", sa.String(10), nullable=True),
        sa.Column("payment_due_date", sa.String(10), nullable=True),
        sa.Column("transfer_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("transfer_status", sa.String(40), nullable=True),
        sa.Column("payment_reference", sa.String(120), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("channel", "transfer_id", name="uq_aggregator_payout"),
        sa.CheckConstraint(
            f"channel IN ({_CHANNELS})", name="ck_aggregator_payout_channel"
        ),
    )
    op.create_index(
        "ix_aggregator_payout_statement", "aggregator_payout", ["statement_id"]
    )

    op.create_table(
        "aggregator_reconciliation",
        _id(),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("external_order_id", sa.String(64), nullable=False),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "aggregator_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("aggregator_order.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "mm_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("match_status", sa.String(20), nullable=False),
        sa.Column("item_discrepancy", postgresql.JSONB(), nullable=True),
        sa.Column(
            "item_flag", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("refund_agg", sa.Numeric(12, 2), nullable=True),
        sa.Column("refund_mm", sa.Numeric(12, 2), nullable=True),
        sa.Column(
            "refund_flag", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("commission_expected", sa.Numeric(12, 2), nullable=True),
        sa.Column("commission_actual", sa.Numeric(12, 2), nullable=True),
        sa.Column("commission_variance", sa.Numeric(12, 2), nullable=True),
        sa.Column("commission_rate_effective", sa.Numeric(6, 4), nullable=True),
        sa.Column("total_agg", sa.Numeric(12, 2), nullable=True),
        sa.Column("total_mm", sa.Numeric(12, 2), nullable=True),
        sa.Column("amount_variance", sa.Numeric(12, 2), nullable=True),
        sa.Column("flags", postgresql.JSONB(), nullable=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("aggregator_sync_run.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "channel", "external_order_id", name="uq_aggregator_reconciliation"
        ),
        sa.CheckConstraint(
            f"channel IN ({_CHANNELS})", name="ck_aggregator_reconciliation_channel"
        ),
        sa.CheckConstraint(
            "match_status IN ('matched', 'unmatched_agg', 'unmatched_mm', "
            "'no_maker_side')",
            name="ck_aggregator_reconciliation_match_status",
        ),
    )
    op.create_index(
        "ix_aggregator_reconciliation_branch",
        "aggregator_reconciliation",
        ["branch_id"],
    )
    op.create_index(
        "ix_aggregator_reconciliation_mm_order",
        "aggregator_reconciliation",
        ["mm_order_id"],
    )


def downgrade() -> None:
    op.drop_table("aggregator_reconciliation")
    op.drop_table("aggregator_payout")
    op.drop_table("aggregator_statement_line")
    op.drop_table("aggregator_statement")
    op.drop_table("aggregator_order_item")
    op.drop_table("aggregator_order")
    op.drop_table("aggregator_sync_run")
    op.drop_table("aggregator_session")
    op.drop_table("foodics_branch_map")
    op.drop_table("aggregator_branch_map")
    op.drop_column("branches", "timezone")
