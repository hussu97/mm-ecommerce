"""The unified item-mapping review queue, across every external system.

One screen for every integration's product/option mappings — GrubOps recipes,
scraped aggregator item names, Foodics skus — all rows of `external_item_map`.
The name matcher (GrubOps) or the promotion path (aggregators) proposes; a human
confirms here, and nothing external acts on a row until `approved`.

Behind `catalogue.manage` — the same authority as editing the menu, since these
rows decide what an external system is told each of our items is.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.permissions import require
from app.models.category import Category
from app.models.external_item_map import (
    EXTERNAL_SYSTEMS,
    KIND_CATEGORY,
    KIND_OPTION,
    KIND_PRODUCT,
    METHOD_MANUAL,
    ExternalItemMap,
)
from app.models.grubops import GrubOpsSyncState
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.product import Product
from app.models.user import User
from app.schemas.item_mapping import (
    ItemMappingList,
    ItemMappingResponse,
    ItemMappingSyncSummary,
    ItemMappingUpdate,
)
from app.services import audit_service
from app.services.grubops import grubops_mapping
from app.services.providers.grubops_provider import GrubOpsError

router = APIRouter()


async def _decorate(db: AsyncSession, rows: list[ExternalItemMap]) -> list[dict]:
    """Attach our own names, and (for GrubOps) the last push result, to a page.

    Done in a few queries over the page rather than per row: the review screen
    shows hundreds at a time and a lazy load per row is what makes a table feel
    broken.
    """
    product_ids = [r.product_id for r in rows if r.product_id]
    option_ids = [r.modifier_option_id for r in rows if r.modifier_option_id]
    category_ids = [r.category_id for r in rows if r.category_id]

    products: dict[uuid.UUID, str] = {}
    if product_ids:
        products = dict(
            (
                await db.execute(
                    select(Product.id, Product.name).where(Product.id.in_(product_ids))
                )
            ).all()
        )

    categories: dict[uuid.UUID, str] = {}
    if category_ids:
        categories = dict(
            (
                await db.execute(
                    select(Category.id, Category.name).where(
                        Category.id.in_(category_ids)
                    )
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
                        GrubOpsSyncState.external_item_map_id.in_([r.id for r in rows])
                    )
                )
            )
            .scalars()
            .all()
        ):
            # One row per branch; the screen wants "is this working", so the most
            # recent word wins and an error is what gets surfaced.
            previous = states.get(state.external_item_map_id)
            if previous is None or state.last_error or not previous.last_error:
                states[state.external_item_map_id] = state

    decorated = []
    for row in rows:
        if row.product_id:
            mm_name, parent = products.get(row.product_id), None
        elif row.category_id:
            mm_name, parent = categories.get(row.category_id), None
        else:
            mm_name, parent = options.get(row.modifier_option_id, (None, None))
        state = states.get(row.id)
        decorated.append(
            {
                **{
                    key: getattr(row, key)
                    for key in (
                        "id",
                        "system",
                        "mm_kind",
                        "product_id",
                        "modifier_option_id",
                        "category_id",
                        "scope",
                        "external_ref",
                        "external_sub_ref",
                        "external_child_ref",
                        "external_type",
                        "external_name",
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


@router.get("", response_model=ItemMappingList)
async def list_mappings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("catalogue.manage")),
    system: str | None = Query(default=None, description="Filter to one system."),
    approved: bool | None = Query(default=None),
    kind: str | None = Query(default=None),
    search: str | None = Query(
        default=None,
        description="Match on our item name, the external name, or an external id.",
    ),
    sort: str = Query(default="queue", pattern="^(queue|name)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=2000),
) -> ItemMappingList:
    """The review queue — needs-decision first, or alphabetical by our name."""
    mm_name_expr = func.coalesce(
        select(Product.name)
        .where(Product.id == ExternalItemMap.product_id)
        .scalar_subquery(),
        select(ModifierOption.name)
        .where(ModifierOption.id == ExternalItemMap.modifier_option_id)
        .scalar_subquery(),
        select(Category.name)
        .where(Category.id == ExternalItemMap.category_id)
        .scalar_subquery(),
    )

    def _scoped(q):
        return q.where(ExternalItemMap.system == system) if system else q

    query = select(ExternalItemMap)
    if system:
        query = query.where(ExternalItemMap.system == system)
    if approved is not None:
        query = query.where(ExternalItemMap.approved.is_(approved))
    if kind is not None:
        query = query.where(ExternalItemMap.mm_kind == kind)
    if search:
        like = f"%{search.strip()}%"
        query = query.where(
            mm_name_expr.ilike(like)
            | ExternalItemMap.external_name.ilike(like)
            | ExternalItemMap.external_ref.ilike(like)
            | ExternalItemMap.external_sub_ref.ilike(like)
        )

    total = (
        await db.execute(select(func.count()).select_from(query.subquery()))
    ).scalar_one()
    approved_count = (
        await db.execute(
            _scoped(
                select(func.count())
                .select_from(ExternalItemMap)
                .where(ExternalItemMap.approved.is_(True))
            )
        )
    ).scalar_one()
    pending_count = (
        await db.execute(
            _scoped(
                select(func.count())
                .select_from(ExternalItemMap)
                .where(ExternalItemMap.approved.is_(False))
            )
        )
    ).scalar_one()

    if sort == "name":
        ordering = (mm_name_expr.asc().nullslast(), ExternalItemMap.external_name.asc())
    else:
        ordering = (
            ExternalItemMap.approved.asc(),
            ExternalItemMap.match_score.asc().nullsfirst(),
        )
    rows = list(
        (
            await db.execute(
                query.order_by(*ordering)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    return ItemMappingList(
        items=[ItemMappingResponse(**d) for d in await _decorate(db, rows)],
        total=total,
        approved_count=approved_count,
        pending_count=pending_count,
    )


@router.put("/{mapping_id}", response_model=ItemMappingResponse)
async def update_mapping(
    mapping_id: uuid.UUID,
    data: ItemMappingUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("catalogue.manage")),
) -> ItemMappingResponse:
    """Correct a guess, or approve it. Any edit marks the row `manual`."""
    row = await db.get(ExternalItemMap, mapping_id)
    if row is None:
        raise NotFoundError("Mapping not found")

    edited = False
    # Re-point the catalogue side: one entity, matching mm_kind, per the CHECK.
    if data.product_id is not None:
        row.product_id = data.product_id or None
        if row.product_id is not None:
            row.mm_kind = KIND_PRODUCT
            row.modifier_option_id = None
            row.category_id = None
        edited = True
    if data.modifier_option_id is not None:
        row.modifier_option_id = data.modifier_option_id or None
        if row.modifier_option_id is not None:
            row.mm_kind = KIND_OPTION
            row.product_id = None
            row.category_id = None
        edited = True
    if data.category_id is not None:
        row.category_id = data.category_id or None
        if row.category_id is not None:
            row.mm_kind = KIND_CATEGORY
            row.product_id = None
            row.modifier_option_id = None
        edited = True
    if data.mm_kind is not None:
        row.mm_kind = data.mm_kind

    # Edit the external identity.
    for f in (
        "external_ref",
        "external_sub_ref",
        "external_child_ref",
        "external_type",
    ):
        value = getattr(data, f)
        if value is not None:
            setattr(row, f, value or None)
            edited = True

    if edited:
        row.match_method = METHOD_MANUAL
        row.match_score = None

    if data.notes is not None:
        row.notes = data.notes

    if data.approved is not None:
        row.approved = data.approved
        row.approved_by = user.email if data.approved else None

    if (
        row.approved
        and row.product_id is None
        and row.modifier_option_id is None
        and row.category_id is None
    ):
        raise BadRequestError(
            "This mapping points at no product, option or category, so there is "
            "nothing to approve — set one first"
        )
    if row.external_ref is None or not str(row.external_ref).strip():
        raise BadRequestError("A mapping needs an external reference")

    await db.flush()

    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="external_item_map",
        entity_id=str(row.id),
        entity_label=row.external_name or str(row.id),
        admin=user,
        changes={
            "system": row.system,
            "approved": row.approved,
            "external_ref": row.external_ref,
            "external_sub_ref": row.external_sub_ref,
            "match_method": row.match_method,
        },
        request=request,
    )

    return ItemMappingResponse(**(await _decorate(db, [row]))[0])


@router.post("/sync", response_model=ItemMappingSyncSummary)
async def sync_mappings(
    system: str = Query(description="Which system to re-match. Only GrubOps today."),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("catalogue.manage")),
) -> ItemMappingSyncSummary:
    """Re-read a system's menu and propose mappings for anything new.

    Only GrubOps has a menu to re-read; the aggregators propose their own rows as
    orders arrive. Safe to press twice: an approved or hand-corrected row is never
    overwritten, only its display name refreshed.
    """
    if system not in EXTERNAL_SYSTEMS:
        raise BadRequestError(f"Unknown system {system!r}")
    if system != "grubops":
        raise BadRequestError(
            f"{system} has no menu to re-match — its mappings are proposed from "
            "incoming orders, not synced"
        )
    try:
        summary = await grubops_mapping.sync_mappings(db)
    except GrubOpsError as exc:
        raise BadRequestError(f"GrubOps could not be read: {exc}") from exc
    return ItemMappingSyncSummary(**summary.as_dict())
