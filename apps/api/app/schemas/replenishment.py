"""Schemas for the replenishment forecast: the per-item guide on the transfer &
production form, its shadow history, and its settings.

Window moments are Dubai-local wall-clock times (no offset), the way the form
shows them.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ForecastBranchLine(BaseModel):
    """One branch's side of an item: what a destination should be sent
    (`kind='transfer'`), or what the source keeps from the morning pool
    (`kind='retain'`)."""

    branch_id: UUID
    branch_name: str
    kind: Literal["transfer", "retain"]
    qty: int
    day_demand_mean: float
    window_start: datetime | None
    window_end: datetime | None
    window_demand_mean: float
    window_demand_quantile: int
    floor: float
    on_hand: float
    target: int
    need: int
    shortfall: int
    #: 0 = everyone's need met; 1 = source's expected demand; 2 = availability
    #: floors; 3 = shared by expected-sales gain. None = got nothing.
    tier: int | None


class ForecastProduction(BaseModel):
    units: float
    batches: int | None
    raw_units: float
    protection_demand_mean: float
    protection_demand_quantile: int
    floors: float
    usable_stock: float
    capped_by_shelf_life: bool
    window_start: datetime | None
    window_end: datetime | None


class ForecastItem(BaseModel):
    item_id: UUID
    item_name: str
    storage_unit: str
    source_on_hand: float
    lines: list[ForecastBranchLine]
    production: ForecastProduction | None
    explain: dict[str, Any]


class ForecastResponse(BaseModel):
    business_date: date
    as_of: datetime
    source_branch_id: UUID
    bucket_hours: int
    service_level: float
    algo_version: str
    #: Business days of demand history the forecast learnt from.
    history_days: int
    items: list[ForecastItem]


# ─── Shadow history ───────────────────────────────────────────────────────────


class ForecastHistoryRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    business_date: date
    source_branch_id: UUID
    branch_id: UUID
    branch_name: str = ""
    item_id: UUID
    item_name: str = ""
    kind: str
    mode: str
    snapshot_at: datetime
    algo_version: str
    forecast_qty: float
    day_demand_mean: float
    window_demand_mean: float
    window_demand_quantile: float
    floor_qty: float
    on_hand_at_snapshot: float
    pool_at_snapshot: float
    shortfall_qty: float
    allocation_tier: int | None
    actual_requested_qty: float | None
    actual_sent_qty: float | None
    actual_planned_qty: float | None
    actual_produced_qty: float | None
    realized_sales: float | None
    est_demand: float | None
    in_stock_share: float | None
    stockout_minutes: int | None
    closing_on_hand: float | None
    baseline_demand: float | None
    evaluated_at: datetime | None


class ForecastAccuracy(BaseModel):
    """Scores over the evaluated rows in the filter."""

    rows: int
    #: Σ|forecast day demand − estimated demand| / Σ estimated demand.
    wape: float | None
    #: Σ(forecast − estimated) / Σ estimated; positive = forecast high.
    bias: float | None
    #: The same for "same weekday last week" — what the forecast must beat.
    baseline_wape: float | None
    stockout_rows: int
    #: Forecast sent more than was sent, and the branch ran out.
    forecast_more_and_ran_out: int
    #: Forecast sent less, and the branch closed with at least the difference left.
    forecast_less_and_surplus: int
    #: Stock-adjusted demand minus sales, where the branch ran out.
    est_lost_sales: float


class ForecastHistoryResponse(BaseModel):
    rows: list[ForecastHistoryRow]
    transfer: ForecastAccuracy
    production: ForecastAccuracy


# ─── Settings ─────────────────────────────────────────────────────────────────


class ReplenishmentSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    bucket_hours: int
    service_level: float
    production_branch_weight: float
    production_branch_id: UUID | None
    same_day_ready_time: time
    next_day_category_ids: list[UUID]
    snapshot_time: time
    half_life_days: int
    window_days: int


class ReplenishmentSettingsUpdate(BaseModel):
    bucket_hours: Literal[1, 2, 3, 4, 6] | None = None
    service_level: float | None = Field(default=None, gt=0.5, lt=1)
    production_branch_weight: float | None = Field(default=None, ge=1, le=5)
    production_branch_id: UUID | None = None
    same_day_ready_time: time | None = None
    next_day_category_ids: list[UUID] | None = None
    snapshot_time: time | None = None
    half_life_days: int | None = Field(default=None, ge=1, le=90)
    window_days: int | None = Field(default=None, ge=14, le=365)
