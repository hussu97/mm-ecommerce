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
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.permissions import require
from app.models.branch import Branch
from app.models.grubops import (
    GRUBOPS_TYPES,
    MATCH_MANUAL,
    GrubOpsItemMap,
    GrubOpsLocationMap,
    GrubOpsSyncState,
)
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.product import Product
from app.models.user import User
from app.schemas.grubops import (
    GrubOpsLocationResponse,
    GrubOpsLocationUpdate,
    GrubOpsMappingList,
    GrubOpsMappingResponse,
    GrubOpsMappingUpdate,
    GrubOpsSyncSummary,
)
from app.services import audit_service, grubops_mapping
from app.services.providers.grubops_provider import GrubOpsError

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


# ── which item is which ───────────────────────────────────────────────────────


async def _decorate(db: AsyncSession, rows: list[GrubOpsItemMap]) -> list[dict]:
    """Attach our own names, and the last sync result, to a page of mappings.

    Done in three queries over the page rather than per row: the review screen
    shows two hundred at a time and a lazy load per row is what makes a table
    feel broken.
    """
    product_ids = [r.product_id for r in rows if r.product_id]
    option_ids = [r.modifier_option_id for r in rows if r.modifier_option_id]

    products: dict[uuid.UUID, str] = {}
    if product_ids:
        products = dict(
            (
                await db.execute(
                    select(Product.id, Product.name).where(Product.id.in_(product_ids))
                )
            ).all()
        )

    options: dict[uuid.UUID, tuple[str, str | None]] = {}
    if option_ids:
        result = (
            await db.execute(
                select(ModifierOption.id, ModifierOption.name, Product.name)
                .join(Modifier, Modifier.id == ModifierOption.modifier_id)
                .join(ProductModifier, ProductModifier.modifier_id == Modifier.id)
                .join(Product, Product.id == ProductModifier.product_id)
                .where(ModifierOption.id.in_(option_ids))
            )
        ).all()
        for option_id, option_name, parent in result:
            options.setdefault(option_id, (option_name, parent))

    states: dict[uuid.UUID, GrubOpsSyncState] = {}
    if rows:
        for state in (
            (
                await db.execute(
                    select(GrubOpsSyncState).where(
                        GrubOpsSyncState.grubops_item_map_id.in_([r.id for r in rows])
                    )
                )
            )
            .scalars()
            .all()
        ):
            # One row per branch; the screen wants "is this working", so the
            # most recent word wins and an error is what gets surfaced.
            previous = states.get(state.grubops_item_map_id)
            if previous is None or state.last_error or not previous.last_error:
                states[state.grubops_item_map_id] = state

    decorated = []
    for row in rows:
        if row.product_id:
            mm_name, parent = products.get(row.product_id), None
        else:
            mm_name, parent = options.get(row.modifier_option_id, (None, None))
        state = states.get(row.id)
        decorated.append(
            {
                **{
                    key: getattr(row, key)
                    for key in (
                        "id",
                        "mm_kind",
                        "product_id",
                        "modifier_option_id",
                        "grubops_brand_id",
                        "grubops_recipe_id",
                        "grubops_modifier_id",
                        "grubops_child_modifier_id",
                        "grubops_type",
                        "grubops_name",
                        "match_method",
                        "approved",
                        "approved_by",
                        "notes",
                    )
                },
                "mm_name": mm_name,
                "mm_parent_name": parent,
                "match_score": (
                    float(row.match_score) if row.match_score is not None else None
                ),
                "last_pushed_at": state.last_pushed_at if state else None,
                "last_error": state.last_error if state else None,
            }
        )
    return decorated


@router.get("/mappings", response_model=GrubOpsMappingList)
async def list_mappings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("catalogue.manage")),
    approved: bool | None = Query(default=None),
    kind: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=2000),
) -> GrubOpsMappingList:
    """The review queue, newest suggestions first."""
    query = select(GrubOpsItemMap)
    if approved is not None:
        query = query.where(GrubOpsItemMap.approved.is_(approved))
    if kind is not None:
        query = query.where(GrubOpsItemMap.mm_kind == kind)

    total = (
        await db.execute(select(func.count()).select_from(query.subquery()))
    ).scalar_one()
    approved_count = (
        await db.execute(
            select(func.count())
            .select_from(GrubOpsItemMap)
            .where(GrubOpsItemMap.approved.is_(True))
        )
    ).scalar_one()
    pending_count = (
        await db.execute(
            select(func.count())
            .select_from(GrubOpsItemMap)
            .where(GrubOpsItemMap.approved.is_(False))
        )
    ).scalar_one()

    rows = list(
        (
            await db.execute(
                query.order_by(
                    # Unapproved first: this is a queue, and the things needing
                    # a decision belong at the top of it.
                    GrubOpsItemMap.approved.asc(),
                    GrubOpsItemMap.match_score.asc().nullsfirst(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    return GrubOpsMappingList(
        items=[GrubOpsMappingResponse(**d) for d in await _decorate(db, rows)],
        total=total,
        approved_count=approved_count,
        pending_count=pending_count,
    )


@router.put("/mappings/{mapping_id}", response_model=GrubOpsMappingResponse)
async def update_mapping(
    mapping_id: uuid.UUID,
    data: GrubOpsMappingUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("catalogue.manage")),
) -> GrubOpsMappingResponse:
    """
    Correct a guess, or approve it.

    Any edit to the GrubOps side marks the row `manual`, which is what stops the
    next run of the matcher treating it as its own suggestion to refresh.
    """
    row = await db.get(GrubOpsItemMap, mapping_id)
    if row is None:
        raise NotFoundError("Mapping not found")

    if data.grubops_type is not None:
        if data.grubops_type not in GRUBOPS_TYPES:
            raise BadRequestError(
                f"Unknown type {data.grubops_type!r}. "
                f"Expected one of {', '.join(GRUBOPS_TYPES)}"
            )
        row.grubops_type = data.grubops_type

    edited = False
    for field in (
        "grubops_recipe_id",
        "grubops_modifier_id",
        "grubops_child_modifier_id",
    ):
        value = getattr(data, field)
        if value is not None:
            setattr(row, field, value or None)
            edited = True
    if edited:
        row.match_method = MATCH_MANUAL
        row.match_score = None

    if data.notes is not None:
        row.notes = data.notes

    if data.approved is not None:
        row.approved = data.approved
        # Stamped with who, because approving is the act that puts a row into
        # service and "who said this was right" is the question afterwards.
        row.approved_by = user.email if data.approved else None

    if row.approved and not (row.grubops_recipe_id or row.grubops_modifier_id):
        raise BadRequestError(
            "This mapping has no GrubOps item on it, so there is nothing to "
            "approve — set a recipe or modifier id first"
        )

    await db.flush()

    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="grubops_item_map",
        entity_id=str(row.id),
        entity_label=row.grubops_name or str(row.id),
        admin=user,
        changes={
            "approved": row.approved,
            "grubops_recipe_id": row.grubops_recipe_id,
            "grubops_modifier_id": row.grubops_modifier_id,
            "grubops_type": row.grubops_type,
            "match_method": row.match_method,
        },
        request=request,
    )

    return GrubOpsMappingResponse(**(await _decorate(db, [row]))[0])


@router.post("/mappings/sync", response_model=GrubOpsSyncSummary)
async def sync_mappings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("catalogue.manage")),
) -> GrubOpsSyncSummary:
    """
    Re-read GrubOps' menu and propose mappings for anything new.

    Safe to press twice, and safe to press after a menu change: an approved or
    hand-corrected row is never overwritten, only its display name is
    refreshed. What comes back is what to go and look at — the counts, and the
    two lists of things that matched nothing on either side.
    """
    try:
        summary = await grubops_mapping.sync_mappings(db)
    except GrubOpsError as exc:
        # Their outage is not our 500 — the screen should say what happened.
        raise BadRequestError(f"GrubOps could not be read: {exc}") from exc
    return GrubOpsSyncSummary(**summary.as_dict())
