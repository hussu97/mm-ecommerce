"""Replenishment forecast: settings, daily demand facts, shadow forecast history.

The admin transfer & production form shows a forecast transfer quantity per
destination and a forecast production quantity per produced good, as a visual
guide. These tables hold its tuning (`replenishment_settings`, one row), the raw
per-day observations it learns from (`replenishment_daily_facts`), and a daily
snapshot of what it forecast, scored later against what was actually done
(`replenishment_forecasts`). None of it is linked to a transfer or production
order. `inventory_items.shelf_life_days` caps the production forecast.

The settings row is seeded once, and only if absent, so it never fights the admin:
the production branch is the single branch with production enabled (left NULL if
there is not exactly one — never a hardcoded reference, which differs between
environments), and the next-day categories are the inventory categories named
"Cookie Melt" (made in the evening, so today's batch only sells tomorrow).

Revision ID: 287_replenishment_forecast
Revises: 286_email_log_reference
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "287_replenishment_forecast"
down_revision: Union[str, None] = "286_email_log_reference"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "inventory_items",
        sa.Column("shelf_life_days", sa.Integer(), nullable=False, server_default="14"),
    )

    op.create_table(
        "replenishment_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("bucket_hours", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "service_level", sa.Numeric(4, 3), nullable=False, server_default="0.900"
        ),
        sa.Column(
            "production_branch_weight",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="1.25",
        ),
        sa.Column(
            "production_branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "same_day_ready_time", sa.Time(), nullable=False, server_default="18:00"
        ),
        sa.Column(
            "next_day_category_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("snapshot_time", sa.Time(), nullable=False, server_default="09:00"),
        sa.Column("half_life_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column("window_days", sa.Integer(), nullable=False, server_default="56"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "bucket_hours IN (1, 2, 3, 4, 6)", name="ck_replenishment_bucket_hours"
        ),
        sa.CheckConstraint(
            "service_level > 0.5 AND service_level < 1",
            name="ck_replenishment_service_level",
        ),
        sa.CheckConstraint(
            "half_life_days > 0 AND window_days >= 14",
            name="ck_replenishment_windows",
        ),
    )

    op.create_table(
        "replenishment_daily_facts",
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inventory_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("business_date", sa.Date(), primary_key=True),
        sa.Column("sales_units", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("bulk_units", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("hourly_units", postgresql.ARRAY(sa.Numeric(14, 4)), nullable=False),
        sa.Column(
            "hourly_open_minutes", postgresql.ARRAY(sa.Integer()), nullable=False
        ),
        sa.Column(
            "hourly_in_stock_minutes", postgresql.ARRAY(sa.Integer()), nullable=True
        ),
        sa.Column("closing_on_hand", sa.Numeric(14, 4), nullable=True),
        sa.Column(
            "unexpanded_line_share",
            sa.Numeric(6, 4),
            nullable=False,
            server_default="0",
        ),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_replenishment_daily_facts_business_date",
        "replenishment_daily_facts",
        ["business_date"],
    )

    op.create_table(
        "replenishment_forecasts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column(
            "source_branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inventory_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("algo_version", sa.String(20), nullable=False),
        sa.Column("forecast_qty", sa.Numeric(14, 4), nullable=False),
        sa.Column("day_demand_mean", sa.Numeric(14, 4), nullable=False),
        sa.Column("window_demand_mean", sa.Numeric(14, 4), nullable=False),
        sa.Column("window_demand_quantile", sa.Numeric(14, 4), nullable=False),
        sa.Column("floor_qty", sa.Numeric(14, 4), nullable=False),
        sa.Column("on_hand_at_snapshot", sa.Numeric(14, 4), nullable=False),
        sa.Column("pool_at_snapshot", sa.Numeric(14, 4), nullable=False),
        sa.Column(
            "shortfall_qty", sa.Numeric(14, 4), nullable=False, server_default="0"
        ),
        sa.Column("allocation_tier", sa.Integer(), nullable=True),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("explain", postgresql.JSONB(), nullable=False),
        sa.Column("actual_requested_qty", sa.Numeric(14, 4), nullable=True),
        sa.Column("actual_sent_qty", sa.Numeric(14, 4), nullable=True),
        sa.Column("actual_planned_qty", sa.Numeric(14, 4), nullable=True),
        sa.Column("actual_produced_qty", sa.Numeric(14, 4), nullable=True),
        sa.Column("realized_sales", sa.Numeric(14, 4), nullable=True),
        sa.Column("est_demand", sa.Numeric(14, 4), nullable=True),
        sa.Column("in_stock_share", sa.Numeric(6, 4), nullable=True),
        sa.Column("stockout_minutes", sa.Integer(), nullable=True),
        sa.Column("closing_on_hand", sa.Numeric(14, 4), nullable=True),
        sa.Column("baseline_demand", sa.Numeric(14, 4), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "business_date",
            "source_branch_id",
            "branch_id",
            "item_id",
            "kind",
            "mode",
            name="uq_replenishment_forecasts_line",
        ),
        sa.CheckConstraint(
            "kind IN ('transfer', 'retain', 'production')",
            name="ck_replenishment_forecasts_kind",
        ),
        sa.CheckConstraint(
            "mode IN ('scheduled', 'backtest')",
            name="ck_replenishment_forecasts_mode",
        ),
    )
    op.create_index(
        "ix_replenishment_forecasts_business_date",
        "replenishment_forecasts",
        ["business_date"],
    )
    op.create_index(
        "ix_replenishment_forecasts_item_id", "replenishment_forecasts", ["item_id"]
    )

    # Seed the one settings row, only if none exists. `gen_random_uuid()` is core
    # since PG13. The production branch is discovered, never a hardcoded reference.
    op.execute(
        """
        INSERT INTO replenishment_settings (id, production_branch_id, next_day_category_ids)
        SELECT
            gen_random_uuid(),
            (
                SELECT CASE WHEN count(*) = 1 THEN (array_agg(branch_id))[1] END
                FROM branch_inventory_settings
                WHERE production_enabled
            ),
            COALESCE(
                (
                    SELECT array_agg(id)
                    FROM inventory_categories
                    WHERE lower(trim(name)) = 'cookie melt' AND deleted_at IS NULL
                ),
                '{}'::uuid[]
            )
        WHERE NOT EXISTS (SELECT 1 FROM replenishment_settings)
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_replenishment_forecasts_item_id", table_name="replenishment_forecasts"
    )
    op.drop_index(
        "ix_replenishment_forecasts_business_date",
        table_name="replenishment_forecasts",
    )
    op.drop_table("replenishment_forecasts")
    op.drop_index(
        "ix_replenishment_daily_facts_business_date",
        table_name="replenishment_daily_facts",
    )
    op.drop_table("replenishment_daily_facts")
    op.drop_table("replenishment_settings")
    op.drop_column("inventory_items", "shelf_life_days")
