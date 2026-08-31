"""Request/response shapes for the catalog & hours sync admin surface.

Read-only drift + the per-item sync toggle in Phase 1; the push endpoint returns a
dry-run plan. Kept in `app/schemas` (not inline) so the generated `@mm/types`
contract carries them (canon rule 8).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class SyncFlagUpdate(BaseModel):
    """Toggle whether a product/category is pushed to the aggregators."""

    sync_to_aggregators: bool
    #: Restrict to specific channels, or null/omit for all the outlet's targets.
    sync_channels: list[str] | None = None


class SyncFlagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    sync_to_aggregators: bool
    sync_channels: list[str] | None = None


class CatalogSyncStatus(BaseModel):
    """The feature's posture — flags + what it would drive."""

    read_enabled: bool
    write_enabled: bool
    enforce_price_parity: bool
    targets: list[str]
    #: Foodics-integrated branches (menu routes to Foodics), by id + name.
    integrated_branches: list[dict[str, str]]


class DriftDelta(BaseModel):
    kind: str
    action: str
    entity: str
    mm_value: str | None = None
    channel_value: str | None = None
    detail: str | None = None


class DriftDiff(BaseModel):
    target: str
    total: int
    summary: dict[str, int] = {}
    deltas: list[DriftDelta] = []


class BranchDriftReport(BaseModel):
    """Menu + hours drift for one branch, per target."""

    branch_id: str
    branch_name: str | None = None
    #: target -> {"menu": DriftDiff|None, "hours": DriftDiff|None, "error": str?}
    targets: dict[str, dict[str, Any]]


class PushPlan(BaseModel):
    """A dry-run push plan (Phase 1 mutates nothing)."""

    dry_run: bool
    target: str
    route: str
    kind: str
    would_apply: list[dict[str, Any]]
    note: str


class WeeklyShift(BaseModel):
    """One open shift. weekday 0=Sunday … 6=Saturday; times HH:MM."""

    weekday: int
    opens: str
    closes: str


class WeeklyHoursUpdate(BaseModel):
    """Replace a branch's whole canonical weekly schedule (empty = fully closed)."""

    shifts: list[WeeklyShift]


class WeeklyHoursResponse(BaseModel):
    branch_id: str
    shifts: list[WeeklyShift]
