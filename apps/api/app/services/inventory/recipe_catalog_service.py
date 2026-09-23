"""Read-only listing of recipe owners for the Recipes console.

The editing/expansion engine lives in ``recipe_service``; this module answers a
different question — "across every product / modifier option / made inventory
item, which has an active recipe, a pending draft, or none?" — so the console can
offer one searchable, filterable, sortable, paginated list per owner kind
instead of the three inline editors it replaces.

It only reads existing tables (owners + ``recipes``/``recipe_versions``/
``recipe_lines``), so there is no migration behind it.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import and_, case, func, null, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import search as search_text
from app.core.exceptions import AppError, BadRequestError
from app.core.money import unit_cost as _cost
from app.models.inventory import InventoryCategory, InventoryItem
from app.models.inventory_v2 import Recipe, RecipeLine, RecipeVersion
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.product import Product
from app.models.user import User
from app.services.inventory import inventory_service, recipe_service

# Only *made* inventory items can own a recipe — purchased kinds
# (raw_material/packaging/resale_good) never do, so the inventory tab lists the
# made kinds alone and never a row that could not have a recipe by design.
_MADE_INVENTORY_KINDS = ("produced_good", "semi_finished")

_ACTIVE_FILTERS = ("all", "active", "inactive")
_RECIPE_FILTERS = ("all", "with", "without")
_SORT_FIELDS = ("name", "recipe_status", "updated")
_SORT_DIRS = ("asc", "desc")


def _owner_config(owner_kind: str):
    """Per-kind column bindings for the one shared query below.

    Returns (from_model, id, name, secondary, kind_or_None, is_active, recipe_fk,
    search_cols, extra_scope, extra_joins).
    """
    if owner_kind == "product":
        return (
            Product,
            Product.id,
            Product.name,
            Product.slug,
            None,
            Product.is_active,
            Recipe.product_id,
            [Product.name, Product.slug],
            [],
            [],
        )
    if owner_kind == "modifier_option":
        return (
            ModifierOption,
            ModifierOption.id,
            ModifierOption.name,
            Modifier.name,  # secondary = the parent modifier's name
            None,
            ModifierOption.is_active,
            Recipe.modifier_option_id,
            [ModifierOption.name, ModifierOption.sku],
            [],
            [(Modifier, Modifier.id == ModifierOption.modifier_id)],
        )
    if owner_kind == "inventory_item":
        return (
            InventoryItem,
            InventoryItem.id,
            InventoryItem.name,
            InventoryItem.sku,
            InventoryItem.kind,
            InventoryItem.is_active,
            Recipe.inventory_item_id,
            [InventoryItem.name, InventoryItem.sku],
            [
                InventoryItem.deleted_at.is_(None),
                InventoryItem.kind.in_(_MADE_INVENTORY_KINDS),
            ],
            [],
        )
    raise BadRequestError(
        "owner_kind must be product, modifier_option or inventory_item"
    )


async def list_recipe_owners(
    db: AsyncSession,
    *,
    owner_kind: str,
    search: str | None = None,
    active: str = "all",
    recipe: str = "all",
    sort: str = "name",
    sort_dir: str = "asc",
    page: int = 1,
    per_page: int = 50,
    branch_id: uuid.UUID | None = None,
) -> tuple[list[dict], int]:
    """One page of recipe owners plus the total that match the filters.

    Recipe cost is priced at ``branch_id``'s own FIFO ingredient costs when
    given, else blended across every branch.
    """
    if active not in _ACTIVE_FILTERS:
        raise BadRequestError(f"active must be one of {_ACTIVE_FILTERS}")
    if recipe not in _RECIPE_FILTERS:
        raise BadRequestError(f"recipe must be one of {_RECIPE_FILTERS}")
    if sort not in _SORT_FIELDS:
        raise BadRequestError(f"sort must be one of {_SORT_FIELDS}")
    if sort_dir not in _SORT_DIRS:
        raise BadRequestError(f"sort_dir must be one of {_SORT_DIRS}")

    (
        from_model,
        id_col,
        name_col,
        secondary_col,
        kind_col,
        active_col,
        fk_col,
        search_cols,
        extra_scope,
        extra_joins,
    ) = _owner_config(owner_kind)

    # One summary row per recipe: its active/draft version numbers and the last
    # time any of its versions was touched (used for the "updated" sort).
    vs = (
        select(
            RecipeVersion.recipe_id.label("recipe_id"),
            func.max(
                case((RecipeVersion.status == "active", RecipeVersion.version_number))
            ).label("active_version"),
            func.max(
                case((RecipeVersion.status == "draft", RecipeVersion.version_number))
            ).label("draft_version"),
            func.max(RecipeVersion.updated_at).label("last_updated"),
        )
        .group_by(RecipeVersion.recipe_id)
        .subquery()
    )

    kind_select = kind_col if kind_col is not None else null()

    core = (
        select(
            id_col.label("id"),
            name_col.label("name"),
            secondary_col.label("secondary"),
            kind_select.label("kind"),
            active_col.label("is_active"),
            Recipe.id.label("recipe_id"),
            vs.c.active_version.label("active_version"),
            vs.c.draft_version.label("draft_version"),
            vs.c.last_updated.label("last_updated"),
        )
        .select_from(from_model)
        .join(
            Recipe,
            and_(Recipe.owner_kind == owner_kind, fk_col == id_col),
            isouter=True,
        )
        .join(vs, vs.c.recipe_id == Recipe.id, isouter=True)
    )
    for join_model, on_clause in extra_joins:
        core = core.join(join_model, on_clause)

    conditions = list(extra_scope)
    if active == "active":
        conditions.append(active_col.is_(True))
    elif active == "inactive":
        conditions.append(active_col.is_(False))
    if recipe == "with":
        conditions.append(Recipe.id.isnot(None))
    elif recipe == "without":
        conditions.append(Recipe.id.is_(None))
    needle = (search or "").strip()
    if needle:
        conditions.append(
            or_(*(search_text.contains(col, needle) for col in search_cols))
        )
    if conditions:
        core = core.where(*conditions)

    total = (
        await db.execute(select(func.count()).select_from(core.subquery()))
    ).scalar_one()

    # A recipe with an active version outranks one with only a draft, which
    # outranks one with none — the natural order for the "recipe_status" sort.
    status_rank = case(
        (vs.c.active_version.isnot(None), 2),
        (vs.c.draft_version.isnot(None), 1),
        else_=0,
    )
    if sort == "name":
        primary = name_col
    elif sort == "recipe_status":
        primary = status_rank
    else:  # updated
        primary = vs.c.last_updated
    primary = primary.desc() if sort_dir == "desc" else primary.asc()
    # Nulls last regardless of direction, then a stable tiebreak on name+id.
    paged = (
        core.order_by(primary.nullslast(), name_col.asc(), id_col.asc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    rows = (await db.execute(paged)).all()

    # The ingredient lines of this page's recipes, in one query — each row's
    # current version (active if present, else draft). Keyed by (recipe_id,
    # status) so the loop below can pick the version that recipe_status reports,
    # and ordered so the summary reads in the recipe's own display order.
    recipe_ids = [row.recipe_id for row in rows if row.recipe_id is not None]
    lines_by_version: dict[tuple[uuid.UUID, str], list[dict]] = {}
    # The rolled-up cost of each shown version's lines, priced at each
    # ingredient's current FIFO cost. This is the cost of the lines *as authored*
    # — one owner unit for a unit-basis version, one whole batch for a
    # batch-basis version — turned into unit/batch figures per row below.
    cost_by_version: dict[tuple[uuid.UUID, str], Decimal] = {}
    if recipe_ids:
        line_rows = (
            await db.execute(
                select(
                    RecipeVersion.recipe_id,
                    RecipeVersion.status,
                    RecipeVersion.basis,
                    RecipeVersion.batch_yield,
                    Recipe.owner_kind,
                    func.coalesce(
                        Recipe.product_id,
                        Recipe.modifier_option_id,
                        Recipe.inventory_item_id,
                    ),
                    InventoryItem,
                    RecipeLine,
                )
                .select_from(RecipeVersion)
                .join(Recipe, Recipe.id == RecipeVersion.recipe_id)
                .join(RecipeLine, RecipeLine.recipe_version_id == RecipeVersion.id)
                .join(InventoryItem, InventoryItem.id == RecipeLine.item_id)
                .where(
                    RecipeVersion.recipe_id.in_(recipe_ids),
                    RecipeVersion.status.in_(("active", "draft")),
                )
                .order_by(
                    RecipeVersion.recipe_id,
                    RecipeVersion.status,
                    RecipeLine.display_order,
                )
            )
        ).all()
        versions: dict[tuple[uuid.UUID, str], dict] = {}
        for rid, status, basis, batch_yield, kind, owner, item, line in line_rows:
            lines_by_version.setdefault((rid, status), []).append(
                {
                    "name": item.name,
                    "quantity": line.quantity,
                    "unit": item.ingredient_unit,
                }
            )
            entry = versions.setdefault(
                (rid, status),
                {
                    "basis": basis,
                    "batch_yield": batch_yield,
                    "kind": kind,
                    "owner": owner,
                    "lines": [],
                },
            )
            entry["lines"].append(line)
        # One recipe rule for every screen (recipe_service.expand_lines): nested
        # phantom sub-recipes, per-line waste and batch yield, costed at one
        # branch's FIFO ingredient costs or blended across all of them.
        catalog = await recipe_service.load_active_catalog(db)
        for _rid, _status, _b, _y, _k, _o, item, _line in line_rows:
            catalog.items.setdefault(item.id, item)
        expanded_by_version = {}
        for key, entry in versions.items():
            try:
                expanded_by_version[key] = recipe_service.expand_lines(
                    catalog,
                    owner_kind=entry["kind"],
                    owner_id=entry["owner"],
                    basis=entry["basis"],
                    batch_yield=entry["batch_yield"],
                    lines=entry["lines"],
                )
            except AppError:
                continue
        warehouse_ids = (
            await inventory_service.branch_warehouse_ids(db, branch_id)
            if branch_id is not None
            else None
        )
        costs = await recipe_service.ingredient_storage_costs(
            db,
            {i for expanded in expanded_by_version.values() for i in expanded},
            warehouse_ids=warehouse_ids,
        )
        for key, expanded in expanded_by_version.items():
            cost_by_version[key] = recipe_service.expansion_cost(
                expanded, catalog.items, costs
            )

    # The basis + batch yield of each shown recipe's active/draft version — a
    # separate query so a version with no lines still reports its basis. Keyed
    # by (recipe_id, status) to match the recipe_status the row will report.
    basis_by_version: dict[tuple[uuid.UUID, str], tuple[str, object]] = {}
    if recipe_ids:
        basis_rows = (
            await db.execute(
                select(
                    RecipeVersion.recipe_id,
                    RecipeVersion.status,
                    RecipeVersion.basis,
                    RecipeVersion.batch_yield,
                ).where(
                    RecipeVersion.recipe_id.in_(recipe_ids),
                    RecipeVersion.status.in_(("active", "draft")),
                )
            )
        ).all()
        for rid, status, basis, batch_yield in basis_rows:
            basis_by_version[(rid, status)] = (basis, batch_yield)

    # For modifier options, the products that carry this option's modifier — so
    # the console can say where the option is used. One query for the page.
    product_names: dict[uuid.UUID, list[str]] = {}
    if owner_kind == "modifier_option":
        option_ids = [row.id for row in rows]
        if option_ids:
            pn_rows = (
                await db.execute(
                    select(ModifierOption.id, Product.name)
                    .select_from(ModifierOption)
                    .join(
                        ProductModifier,
                        ProductModifier.modifier_id == ModifierOption.modifier_id,
                    )
                    .join(Product, Product.id == ProductModifier.product_id)
                    .where(ModifierOption.id.in_(option_ids))
                    .order_by(Product.name)
                )
            ).all()
            for option_id, product_name in pn_rows:
                product_names.setdefault(option_id, []).append(product_name)

    items: list[dict] = []
    for row in rows:
        has_recipe = row.recipe_id is not None
        if row.active_version is not None:
            recipe_status = "active"
        elif row.draft_version is not None:
            recipe_status = "draft"
        else:
            recipe_status = "none"
        ingredients = (
            lines_by_version.get((row.recipe_id, recipe_status), [])
            if has_recipe and recipe_status != "none"
            else []
        )
        basis, batch_yield = (
            basis_by_version.get((row.recipe_id, recipe_status), ("unit", None))
            if has_recipe and recipe_status != "none"
            else ("unit", None)
        )
        # Cost of one owner unit from the current version; a batch-basis
        # version also shows what one whole batch costs. None with no recipe.
        unit_cost: Decimal | None = None
        batch_cost: Decimal | None = None
        if has_recipe and recipe_status != "none":
            unit_cost = _cost(
                cost_by_version.get((row.recipe_id, recipe_status), Decimal("0"))
            )
            if basis == "batch" and batch_yield and Decimal(str(batch_yield)) > 0:
                batch_cost = _cost(unit_cost * Decimal(str(batch_yield)))
        items.append(
            {
                "id": row.id,
                "name": row.name,
                "secondary": row.secondary,
                "product_names": product_names.get(row.id, []),
                "kind": row.kind,
                "is_active": bool(row.is_active),
                "has_recipe": has_recipe,
                "recipe_status": recipe_status,
                "active_version_number": row.active_version,
                "draft_version_number": row.draft_version,
                "basis": basis,
                "batch_yield": batch_yield,
                "unit_cost": unit_cost,
                "batch_cost": batch_cost,
                "line_count": len(ingredients),
                "ingredients": ingredients,
            }
        )

    return items, total


async def list_active_inventory_recipes(db: AsyncSession) -> list[dict]:
    """The read-only recipe cards the POS Recipes tab shows.

    Made inventory items (produced/semi-finished) whose recipe has a live
    ``active`` version — one card per item, with that version's number, when
    and by whom it was activated, and its ingredient lines. Purchased kinds
    and items without an active recipe are excluded by design: the shop floor
    only ever sees the one live way to build a thing.

    Recipes are global, so this takes no branch — the branch flag gates only
    whether the tab is shown, never which recipes exist.
    """
    head_rows = (
        await db.execute(
            select(
                InventoryItem.id,
                InventoryItem.name,
                InventoryItem.sku,
                RecipeVersion.id.label("version_id"),
                RecipeVersion.version_number,
                RecipeVersion.activated_at,
                RecipeVersion.basis,
                RecipeVersion.batch_yield,
                User.display_name,
                User.email,
                InventoryCategory.name.label("category_name"),
            )
            .select_from(InventoryItem)
            .join(
                Recipe,
                and_(
                    Recipe.owner_kind == "inventory_item",
                    Recipe.inventory_item_id == InventoryItem.id,
                ),
            )
            .join(
                RecipeVersion,
                and_(
                    RecipeVersion.recipe_id == Recipe.id,
                    RecipeVersion.status == "active",
                ),
            )
            .join(User, User.id == RecipeVersion.activated_by, isouter=True)
            .join(
                InventoryCategory,
                InventoryCategory.id == InventoryItem.category_id,
                isouter=True,
            )
            .where(
                InventoryItem.deleted_at.is_(None),
                InventoryItem.kind.in_(_MADE_INVENTORY_KINDS),
                InventoryItem.is_active.is_(True),
            )
            .order_by(InventoryItem.name.asc())
        )
    ).all()

    version_ids = [row.version_id for row in head_rows]
    lines_by_version: dict[uuid.UUID, list[dict]] = {}
    if version_ids:
        # The ingredient lines of every card in one query. The line's item is a
        # different InventoryItem row than the card's owner, so this second
        # query joins InventoryItem afresh with no alias clash.
        line_rows = (
            await db.execute(
                select(
                    RecipeLine.recipe_version_id,
                    InventoryItem.name,
                    RecipeLine.quantity,
                    InventoryItem.ingredient_unit,
                    RecipeLine.yield_percentage,
                )
                .select_from(RecipeLine)
                .join(InventoryItem, InventoryItem.id == RecipeLine.item_id)
                .where(RecipeLine.recipe_version_id.in_(version_ids))
                .order_by(RecipeLine.recipe_version_id, RecipeLine.display_order)
            )
        ).all()
        for vid, name, quantity, unit, yield_pct in line_rows:
            lines_by_version.setdefault(vid, []).append(
                {
                    "name": name,
                    "quantity": quantity,
                    "unit": unit,
                    "yield_percentage": yield_pct,
                }
            )

    cards: list[dict] = []
    for row in head_rows:
        ingredients = lines_by_version.get(row.version_id, [])
        cards.append(
            {
                "item_id": row.id,
                "name": row.name,
                "sku": row.sku,
                "category_name": row.category_name,
                "basis": row.basis,
                "batch_yield": row.batch_yield,
                "version_number": row.version_number,
                "activated_at": row.activated_at,
                "activated_by_name": row.display_name or row.email,
                "line_count": len(ingredients),
                "ingredients": ingredients,
            }
        )
    return cards
