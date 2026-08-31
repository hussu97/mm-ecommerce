"""Admin surface for the catalog & hours sync — read-only drift + the sync toggle.

Phase 1: report the drift between MM's flagged catalogue and each integrator, and
let an operator opt a product/category into the sync. The refresh (live read) and
push (write) endpoints exist but are hard-gated — refresh 503s unless
`CATALOG_SYNC_READ_ENABLED`, push 503s unless `CATALOG_SYNC_ENABLED` and even then
returns a dry-run plan. Everything behind `catalogue.manage`, the same permission
the aggregator catalogue endpoints use.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_db
from app.core.exceptions import NotFoundError
from app.core.permissions import require
from app.models.branch import Branch
from app.models.catalog_sync import SNAPSHOT_MENU, SYNC_TARGETS
from app.models.category import Category
from app.models.product import Product
from app.models.user import User
from app.schemas.catalog_sync import (
    BranchDriftReport,
    CatalogSyncStatus,
    PushPlan,
    SyncFlagResponse,
    SyncFlagUpdate,
    WeeklyHoursResponse,
    WeeklyHoursUpdate,
    WeeklyShift,
)
from app.services import audit_service
from app.services.aggregators import catalog_sync

router = APIRouter()


def _parse_targets(targets: str | None) -> list[str] | None:
    if not targets:
        return None
    return [t.strip() for t in targets.split(",") if t.strip()]


@router.get("/status", response_model=CatalogSyncStatus)
async def sync_status(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("catalogue.manage")),
) -> CatalogSyncStatus:
    """The feature's posture: flags, targets, and the integrated branches."""
    branches = await catalog_sync.integrated_branches(db)
    return CatalogSyncStatus(
        read_enabled=settings.CATALOG_SYNC_READ_ENABLED,
        write_enabled=settings.CATALOG_SYNC_ENABLED,
        enforce_price_parity=settings.CATALOG_SYNC_ENFORCE_PRICE_PARITY,
        targets=list(SYNC_TARGETS),
        integrated_branches=[{"id": str(b.id), "name": b.name} for b in branches],
    )


@router.get("/drift", response_model=BranchDriftReport)
async def branch_drift(
    branch_id: UUID,
    targets: str | None = Query(
        None, description="Comma-separated targets; default all."
    ),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("catalogue.manage")),
) -> BranchDriftReport:
    """Menu + hours drift for one branch, per target — computed from snapshots.

    Read-only: it diffs MM's flagged catalogue against the last stored read of
    each integrator. Targets with no snapshot yet come back with null diffs
    (nothing has been read for them).
    """
    branch = (
        await db.execute(select(Branch).where(Branch.id == branch_id))
    ).scalar_one_or_none()
    if branch is None:
        raise NotFoundError("Branch not found")
    report = await catalog_sync.compute_drift_all(
        db, branch_id=branch_id, targets=_parse_targets(targets)
    )
    return BranchDriftReport(
        branch_id=str(branch_id), branch_name=branch.name, targets=report
    )


@router.post("/refresh", response_model=dict)
async def refresh(
    branch_id: UUID,
    targets: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("catalogue.manage")),
) -> dict:
    """Read each integrator's live menu/hours into a fresh snapshot. Gated.

    503s unless `CATALOG_SYNC_READ_ENABLED` — it opens marketplace sessions. Per
    target isolated, so one dead session never blocks the rest.
    """
    return await catalog_sync.refresh_all(
        db, branch_id=branch_id, targets=_parse_targets(targets)
    )


@router.post("/push", response_model=PushPlan)
async def push(
    target: str,
    branch_id: UUID | None = None,
    kind: str = SNAPSHOT_MENU,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("catalogue.manage")),
) -> PushPlan:
    """Write MM's menu/hours to one target. Hard-gated; dry-run in Phase 1.

    503s unless `CATALOG_SYNC_ENABLED`. Even enabled, Phase 1 returns the plan it
    *would* apply (routed per the audit rule) and mutates nothing.
    """
    plan = await catalog_sync.plan_push(
        db, target=target, branch_id=branch_id, kind=kind
    )
    return PushPlan(**plan)


@router.put("/products/{product_id}/sync", response_model=SyncFlagResponse)
async def set_product_sync(
    product_id: UUID,
    payload: SyncFlagUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("catalogue.manage")),
) -> SyncFlagResponse:
    """Opt a product into (or out of) the aggregator sync."""
    product = (
        await db.execute(select(Product).where(Product.id == product_id))
    ).scalar_one_or_none()
    if product is None:
        raise NotFoundError("Product not found")
    before = {
        "sync_to_aggregators": product.sync_to_aggregators,
        "sync_channels": product.sync_channels,
    }
    product.sync_to_aggregators = payload.sync_to_aggregators
    product.sync_channels = payload.sync_channels
    await db.flush()
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="product",
        entity_id=str(product.id),
        entity_label=f"{product.name} — aggregator sync",
        admin=admin,
        changes={"before": before, "after": payload.model_dump()},
        request=request,
    )
    return SyncFlagResponse(
        id=str(product.id),
        name=product.name,
        sync_to_aggregators=product.sync_to_aggregators,
        sync_channels=product.sync_channels,
    )


@router.put("/categories/{category_id}/sync", response_model=SyncFlagResponse)
async def set_category_sync(
    category_id: UUID,
    payload: SyncFlagUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("catalogue.manage")),
) -> SyncFlagResponse:
    """Opt a category into (or out of) the aggregator sync."""
    category = (
        await db.execute(select(Category).where(Category.id == category_id))
    ).scalar_one_or_none()
    if category is None:
        raise NotFoundError("Category not found")
    before = {
        "sync_to_aggregators": category.sync_to_aggregators,
        "sync_channels": category.sync_channels,
    }
    category.sync_to_aggregators = payload.sync_to_aggregators
    category.sync_channels = payload.sync_channels
    await db.flush()
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="category",
        entity_id=str(category.id),
        entity_label=f"{category.name} — aggregator sync",
        admin=admin,
        changes={"before": before, "after": payload.model_dump()},
        request=request,
    )
    return SyncFlagResponse(
        id=str(category.id),
        name=category.name,
        sync_to_aggregators=category.sync_to_aggregators,
        sync_channels=category.sync_channels,
    )


@router.get("/branches/{branch_id}/hours", response_model=WeeklyHoursResponse)
async def get_weekly_hours(
    branch_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("catalogue.manage")),
) -> WeeklyHoursResponse:
    """The branch's canonical per-day marketplace schedule (source of truth)."""
    rows = await catalog_sync.get_weekly_hours(db, branch_id)
    return WeeklyHoursResponse(
        branch_id=str(branch_id),
        shifts=[
            WeeklyShift(weekday=r.weekday, opens=r.opens, closes=r.closes) for r in rows
        ],
    )


@router.put("/branches/{branch_id}/hours", response_model=WeeklyHoursResponse)
async def set_weekly_hours(
    branch_id: UUID,
    payload: WeeklyHoursUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("catalogue.manage")),
) -> WeeklyHoursResponse:
    """Replace the branch's weekly schedule (a weekday with no shift = closed)."""
    rows = await catalog_sync.set_weekly_hours(
        db, branch_id, [s.model_dump() for s in payload.shifts]
    )
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="branch",
        entity_id=str(branch_id),
        entity_label="marketplace weekly hours",
        admin=admin,
        changes={"shifts": [s.model_dump() for s in payload.shifts]},
        request=request,
    )
    return WeeklyHoursResponse(
        branch_id=str(branch_id),
        shifts=[
            WeeklyShift(weekday=r.weekday, opens=r.opens, closes=r.closes) for r in rows
        ],
    )
