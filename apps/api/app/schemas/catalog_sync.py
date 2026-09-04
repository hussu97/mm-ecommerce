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


class CreateItemRequest(BaseModel):
    """Create one MM product on a target.

    `target=foodics` (default) is the master path for the integrated branches;
    `target=careem` creates directly on a non-Foodics Careem outlet (needs
    `branch_id`). `dry_run` (the default) returns the exact create it would POST and
    mutates nothing; `dry_run=False` needs `CATALOG_SYNC_ENABLED`.
    """

    product_id: str
    target: str = "foodics"
    branch_id: str | None = None
    dry_run: bool = True


class MappingResolveResult(BaseModel):
    """What `resolve` approved from a target's last menu read."""

    system: str
    products_matched: int
    products_unmatched: list[str] = []
    options_matched: int
    options_unmatched: list[str] = []
    categories_matched: int
    approved: int
