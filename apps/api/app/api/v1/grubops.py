"""
The console's view of the GrubOps map: which branches sync, and which item is which.

Two screens' worth of API. The **locations** half is the per-branch switch —
the register is live in Sharjah and not yet in Barsha Heights, and a branch
whose staff are not marking things out on the terminal has nothing true to say
about its stock. The **mappings** half is the review queue: the name matcher
proposes, somebody confirms, and nothing is pushed until they have.

Behind `catalogue.manage` rather than a permission of its own. This is the same
authority as editing the menu — the people who decide what is sold are the
people who should decide what the aggregators are told about it — and adding a
permission nobody has assigned yet would only mean nobody could reach the
screen.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.exceptions import NotFoundError
from app.core.permissions import require
from app.models.branch import Branch
from app.models.grubops import (
    GrubOpsLocationMap,
)
from app.models.grubops_order import GrubOpsOrderMap
from app.models.order import Order, OrderItem
from app.models.user import User
from app.schemas.grubops import (
    GrubOpsLocationResponse,
    GrubOpsLocationUpdate,
    GrubOpsOrderList,
    GrubOpsOrderRow,
)
from app.services import audit_service

router = APIRouter()


# ── which branches sync ───────────────────────────────────────────────────────


@router.get("/locations", response_model=list[GrubOpsLocationResponse])
async def list_locations(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("catalogue.manage")),
) -> list[GrubOpsLocationResponse]:
    """Every branch known to GrubOps, and whether its stock is being mirrored."""
    rows = (
        await db.execute(
            select(GrubOpsLocationMap, Branch)
            .join(Branch, Branch.id == GrubOpsLocationMap.branch_id)
            .order_by(Branch.name)
        )
    ).all()
    return [
        GrubOpsLocationResponse(
            id=location.id,
            branch_id=location.branch_id,
            branch_name=branch.name,
            branch_reference=branch.reference,
            grubops_location_id=location.grubops_location_id,
            grubops_partner_id=location.grubops_partner_id,
            is_active=location.is_active,
        )
        for location, branch in rows
    ]


@router.put("/locations/{location_id}", response_model=GrubOpsLocationResponse)
async def update_location(
    location_id: uuid.UUID,
    data: GrubOpsLocationUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("catalogue.manage")),
) -> GrubOpsLocationResponse:
    """
    Turn one branch's sync on or off.

    Switching a branch **on** does not push anything by itself: the reconcile
    loop picks it up on its next tick and sends whatever differs from what
    GrubOps was last told, which for a branch that has never synced is its whole
    approved map. Switching it **off** stops the pushing and leaves GrubOps
    holding whatever it last heard — deliberately, because the alternative is a
    switch that silently puts a shop's entire menu back on sale.
    """
    location = await db.get(GrubOpsLocationMap, location_id)
    if location is None:
        raise NotFoundError("That branch is not mapped to GrubOps")

    branch = await db.get(Branch, location.branch_id)

    if data.is_active is not None:
        location.is_active = data.is_active
    if data.grubops_location_id is not None:
        location.grubops_location_id = data.grubops_location_id

    await db.flush()

    # Worth a record: "why did Barsha stop updating on Talabat" is a question
    # with a name and a time on the answer.
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="grubops_location",
        entity_id=str(location.id),
        entity_label=f"GrubOps sync @ {branch.reference if branch else location.branch_id}",
        admin=user,
        changes={
            "is_active": location.is_active,
            "grubops_location_id": location.grubops_location_id,
        },
        request=request,
    )

    return GrubOpsLocationResponse(
        id=location.id,
        branch_id=location.branch_id,
        branch_name=branch.name if branch else None,
        branch_reference=branch.reference if branch else None,
        grubops_location_id=location.grubops_location_id,
        grubops_partner_id=location.grubops_partner_id,
        is_active=location.is_active,
    )


# ── ingested aggregator orders (read-only monitoring) ─────────────────────────


@router.get("/orders", response_model=GrubOpsOrderList)
async def list_grubops_orders(
    channel: str | None = Query(default=None),
    errors_only: bool = Query(default=False),
    unmapped_only: bool = Query(default=False),
    search: str | None = Query(
        default=None,
        description="Match on the external id, channel, GrubOps id or status.",
    ),
    sort: str = Query(
        default="recent",
        pattern="^(recent|channel)$",
        description="`recent` (newest first) or `channel` (alphabetical).",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("catalogue.manage")),
) -> GrubOpsOrderList:
    """The order ingest's own log: what came in, where it landed, what failed.

    Read-only. The ingest loop owns these rows; this screen is for spotting the
    two things a person needs to see — an order whose write-back errored, and an
    order carrying a line no mapping could resolve.
    """
    # An order has unmapped lines if any of its items resolved to no product.
    unmapped_subq = (
        select(OrderItem.order_id)
        .where(OrderItem.product_id.is_(None))
        .distinct()
        .subquery()
    )

    filters = []
    if channel:
        filters.append(GrubOpsOrderMap.source_channel == channel)
    if errors_only:
        filters.append(GrubOpsOrderMap.last_push_error.isnot(None))
    if unmapped_only:
        filters.append(
            GrubOpsOrderMap.mm_order_id.in_(select(unmapped_subq.c.order_id))
        )
    if search:
        like = f"%{search.strip()}%"
        filters.append(
            GrubOpsOrderMap.external_id.ilike(like)
            | GrubOpsOrderMap.source_channel.ilike(like)
            | GrubOpsOrderMap.grubops_order_id.ilike(like)
            | GrubOpsOrderMap.last_grubops_status.ilike(like)
        )

    base = select(GrubOpsOrderMap)
    for f in filters:
        base = base.where(f)

    order_by = (
        (
            GrubOpsOrderMap.source_channel.asc().nullslast(),
            GrubOpsOrderMap.created_at.desc(),
        )
        if sort == "channel"
        else (GrubOpsOrderMap.created_at.desc(),)
    )

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    error_count = (
        await db.execute(
            select(func.count())
            .select_from(GrubOpsOrderMap)
            .where(GrubOpsOrderMap.last_push_error.isnot(None))
        )
    ).scalar_one()
    unmapped_count = (
        await db.execute(select(func.count(func.distinct(unmapped_subq.c.order_id))))
    ).scalar_one()

    rows = (
        (
            await db.execute(
                base.order_by(*order_by).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    # Resolve MM order numbers and unmapped flags in one query each, never per
    # row.
    order_ids = [r.mm_order_id for r in rows if r.mm_order_id]
    numbers: dict = {}
    unmapped_ids: set = set()
    if order_ids:
        for oid, num in (
            await db.execute(
                select(Order.id, Order.order_number).where(Order.id.in_(order_ids))
            )
        ).all():
            numbers[oid] = num
        unmapped_ids = {
            oid
            for (oid,) in (
                await db.execute(
                    select(OrderItem.order_id)
                    .where(
                        OrderItem.order_id.in_(order_ids),
                        OrderItem.product_id.is_(None),
                    )
                    .distinct()
                )
            ).all()
        }

    items = []
    for r in rows:
        row = GrubOpsOrderRow.model_validate(r)
        row.mm_order_number = numbers.get(r.mm_order_id)
        row.has_unmapped_lines = r.mm_order_id in unmapped_ids
        items.append(row)

    return GrubOpsOrderList(
        items=items,
        total=total,
        error_count=error_count,
        unmapped_count=unmapped_count,
    )
