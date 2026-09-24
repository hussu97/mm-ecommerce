"""Replenishment forecast: settings, the daily demand facts it learns from, and the
shadow history of what it forecast against what was actually done.

Nothing here drives stock. The forecast is a visual guide on the admin transfer &
production form; `replenishment_forecasts` records it once a day so it can be
scored later, and is deliberately NOT linked to any transfer or production order —
actuals are matched back by date, item and branch.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class ReplenishmentSettings(Base, UUIDMixin, TimestampMixin):
    """The one row of forecast tuning, edited from the admin history page."""

    __tablename__ = "replenishment_settings"

    #: Width of the intraday demand bucket, starting at each branch's opening
    #: time. Wider buckets pool more sales per bucket, so the profile is steadier.
    bucket_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="3"
    )
    #: The demand quantile a target stock covers.
    service_level: Mapped[Any] = mapped_column(
        Numeric(4, 3), nullable=False, server_default="0.900"
    )
    #: Extra weight the production branch gets when scarce stock is shared out.
    production_branch_weight: Mapped[Any] = mapped_column(
        Numeric(5, 2), nullable=False, server_default="1.25"
    )
    #: The pool every transfer leaves from and where production happens. NULL
    #: means "the form's chosen source branch".
    production_branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: When the day's production becomes sellable at the production branch.
    same_day_ready_time: Mapped[Any] = mapped_column(
        Time, nullable=False, server_default="18:00"
    )
    #: Inventory categories whose production only sells from the next day
    #: (cookie melt is made in the evening).
    next_day_category_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    #: When the daily shadow snapshot is taken (Dubai time).
    snapshot_time: Mapped[Any] = mapped_column(
        Time, nullable=False, server_default="09:00"
    )
    half_life_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="14"
    )
    window_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="56"
    )

    __table_args__ = (
        CheckConstraint(
            "bucket_hours IN (1, 2, 3, 4, 6)", name="ck_replenishment_bucket_hours"
        ),
        CheckConstraint(
            "service_level > 0.5 AND service_level < 1",
            name="ck_replenishment_service_level",
        ),
        CheckConstraint(
            "half_life_days > 0 AND window_days >= 14",
            name="ck_replenishment_windows",
        ),
    )


class ReplenishmentDailyFact(Base):
    """What one branch sold of one produced good on one business day, by local
    clock hour, and how many of each hour's open minutes it had stock.

    Raw observations only. Everything derived — the intraday profile, the
    stock-out-adjusted demand — depends on settings (the bucket width) and is
    computed when read, so changing a setting never leaves stale numbers here.
    Kept permanently: it is the only availability history that outlives the
    90-day audit log.
    """

    __tablename__ = "replenishment_daily_facts"

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        primary_key=True,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    business_date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    #: Units sold (storage units), excluding bulk orders.
    sales_units: Mapped[Any] = mapped_column(
        Numeric(14, 4), nullable=False, server_default="0"
    )
    #: Units in single orders big enough to be a one-off, kept out of demand.
    bulk_units: Mapped[Any] = mapped_column(
        Numeric(14, 4), nullable=False, server_default="0"
    )
    #: Index = local clock hour 0..23 of the order's creation.
    hourly_units: Mapped[list[Any]] = mapped_column(
        ARRAY(Numeric(14, 4)), nullable=False
    )
    hourly_open_minutes: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), nullable=False
    )
    #: Open minutes in each hour with stock > 0. NULL when the branch's stock was
    #: not yet known (before its first physical count).
    hourly_in_stock_minutes: Mapped[list[int] | None] = mapped_column(
        ARRAY(Integer), nullable=True
    )
    closing_on_hand: Mapped[Any | None] = mapped_column(Numeric(14, 4), nullable=True)
    #: Sale lines at the branch that day that could not be expanded to produced
    #: goods (unmapped aggregator line, missing recipe), over all sale lines.
    unexpanded_line_share: Mapped[Any] = mapped_column(
        Numeric(6, 4), nullable=False, server_default="0"
    )
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReplenishmentForecast(Base, UUIDMixin):
    """One forecast line as it stood at the daily snapshot, and what then happened.

    `kind='transfer'` rows are per destination branch; `kind='retain'` is what the
    production branch keeps back from the morning pool; `kind='production'` is the
    day's production at the production branch. Actual and outcome columns are
    filled by the nightly evaluator.
    """

    __tablename__ = "replenishment_forecasts"

    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source_branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    algo_version: Mapped[str] = mapped_column(String(20), nullable=False)

    forecast_qty: Mapped[Any] = mapped_column(Numeric(14, 4), nullable=False)
    #: The whole-day demand forecast for this branch (all branches for production).
    day_demand_mean: Mapped[Any] = mapped_column(Numeric(14, 4), nullable=False)
    window_demand_mean: Mapped[Any] = mapped_column(Numeric(14, 4), nullable=False)
    window_demand_quantile: Mapped[Any] = mapped_column(Numeric(14, 4), nullable=False)
    floor_qty: Mapped[Any] = mapped_column(Numeric(14, 4), nullable=False)
    on_hand_at_snapshot: Mapped[Any] = mapped_column(Numeric(14, 4), nullable=False)
    pool_at_snapshot: Mapped[Any] = mapped_column(Numeric(14, 4), nullable=False)
    shortfall_qty: Mapped[Any] = mapped_column(
        Numeric(14, 4), nullable=False, server_default="0"
    )
    allocation_tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    explain: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    actual_requested_qty: Mapped[Any | None] = mapped_column(Numeric(14, 4))
    actual_sent_qty: Mapped[Any | None] = mapped_column(Numeric(14, 4))
    actual_planned_qty: Mapped[Any | None] = mapped_column(Numeric(14, 4))
    actual_produced_qty: Mapped[Any | None] = mapped_column(Numeric(14, 4))
    realized_sales: Mapped[Any | None] = mapped_column(Numeric(14, 4))
    est_demand: Mapped[Any | None] = mapped_column(Numeric(14, 4))
    in_stock_share: Mapped[Any | None] = mapped_column(Numeric(6, 4))
    stockout_minutes: Mapped[int | None] = mapped_column(Integer)
    closing_on_hand: Mapped[Any | None] = mapped_column(Numeric(14, 4))
    #: Same weekday last week's sales — the naive baseline the forecast must beat.
    baseline_demand: Mapped[Any | None] = mapped_column(Numeric(14, 4))
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "business_date",
            "source_branch_id",
            "branch_id",
            "item_id",
            "kind",
            "mode",
            name="uq_replenishment_forecasts_line",
        ),
        CheckConstraint(
            "kind IN ('transfer', 'retain', 'production')",
            name="ck_replenishment_forecasts_kind",
        ),
        CheckConstraint(
            "mode IN ('scheduled', 'backtest')",
            name="ck_replenishment_forecasts_mode",
        ),
    )
