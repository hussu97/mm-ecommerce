"""Versioned recipe editing, validation, recursive expansion and snapshots.

Recipe lines can only name inventory items. Expansion stops at a stocked item
and walks through a phantom item, which is the boundary that prevents a finished
good's raw materials being consumed once at production and again at sale.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import (
    AppError,
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from app.core.money import quantity as quantize_quantity
from app.core.money import unit_cost as quantize_cost
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryItemIngredient,
    InventoryLevel,
    Warehouse,
)
from app.models.inventory_v2 import (
    InventoryTrackingModeEnum,
    Recipe,
    RecipeBasisEnum,
    RecipeCatalogState,
    RecipeLine,
    RecipeOwnerKindEnum,
    RecipeVersion,
    RecipeVersionStatusEnum,
)
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.user import User

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RecipeLineInput:
    item_id: uuid.UUID
    quantity: Decimal
    inactive_in_order_types: list[str] = field(default_factory=list)
    yield_percentage: Decimal = Decimal("1")
    display_order: int = 0
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExpandedLine:
    item_id: uuid.UUID
    quantity: Decimal
    planned_waste: Decimal = Decimal("0")
    recipe_version_ids: set[uuid.UUID] = field(default_factory=set)
    paths: list[list[dict[str, str]]] = field(default_factory=list)

    def as_snapshot(self) -> dict[str, Any]:
        return {
            "item_id": str(self.item_id),
            "quantity": str(quantize_quantity(self.quantity)),
            "planned_waste": str(quantize_quantity(self.planned_waste)),
            "recipe_version_ids": sorted(
                str(value) for value in self.recipe_version_ids
            ),
            "paths": self.paths,
        }


@dataclass(slots=True)
class ActiveRecipeCatalog:
    """The active recipe graph loaded in two queries for one expansion job."""

    versions: dict[tuple[str, uuid.UUID], RecipeVersion]
    items: dict[uuid.UUID, InventoryItem]


def _owner_column(kind: str):
    if kind == RecipeOwnerKindEnum.PRODUCT.value:
        return Recipe.product_id
    if kind == RecipeOwnerKindEnum.MODIFIER_OPTION.value:
        return Recipe.modifier_option_id
    if kind == RecipeOwnerKindEnum.INVENTORY_ITEM.value:
        return Recipe.inventory_item_id
    raise BadRequestError(f"Unknown recipe owner kind '{kind}'")


#: Inventory kinds a recipe must never be created for: they are bought and counted
#: directly, not produced, so a recipe on one would double-count its own stock (the
#: item would be consumed AND expand into ingredients). Only a produced_good or a
#: semi_finished item is *made* from other things and so carries a recipe. This is
#: the maker-checker on recipe creation — enforced on the write, since a stocked
#: retail/raw/packaging item having a recipe is a data error by construction.
_PURCHASED_ITEM_KINDS = frozenset({"raw_material", "packaging", "resale_good"})


async def current_catalog_generation(db: AsyncSession) -> int:
    """Return the durable generation of the active recipe graph."""
    generation = await db.scalar(
        select(RecipeCatalogState.generation).where(RecipeCatalogState.id == 1)
    )
    if generation is None:
        raise RuntimeError("Recipe catalog generation singleton is missing")
    return int(generation)


async def _bump_catalog_generation(db: AsyncSession) -> int:
    """Advance the graph clock in the same transaction as an activation."""
    generation = await db.scalar(
        update(RecipeCatalogState)
        .where(RecipeCatalogState.id == 1)
        .values(generation=RecipeCatalogState.generation + 1)
        .returning(RecipeCatalogState.generation)
    )
    if generation is None:
        raise RuntimeError("Recipe catalog generation singleton is missing")
    return int(generation)


async def _assert_owner_exists(
    db: AsyncSession, kind: str, owner_id: uuid.UUID
) -> None:
    model = {
        RecipeOwnerKindEnum.PRODUCT.value: Product,
        RecipeOwnerKindEnum.MODIFIER_OPTION.value: ModifierOption,
        RecipeOwnerKindEnum.INVENTORY_ITEM.value: InventoryItem,
    }.get(kind)
    if model is None:
        raise BadRequestError(f"Unknown recipe owner kind '{kind}'")
    owner = (
        await db.execute(select(model).where(model.id == owner_id).with_for_update())
    ).scalar_one_or_none()
    if owner is None:
        raise NotFoundError(f"{kind.replace('_', ' ').title()} not found")
    if isinstance(owner, InventoryItem) and owner.kind in _PURCHASED_ITEM_KINDS:
        raise BadRequestError(
            f"'{owner.name}' is a {owner.kind.replace('_', ' ')} — a purchased item "
            "counted directly in stock, so it has no recipe. A recipe describes how a "
            "produced or semi-finished item is made from other items."
        )


async def owner_name(db: AsyncSession, kind: str, owner_id: uuid.UUID) -> str:
    """The owner's display name for an audit-log label, or the id if unknown."""
    model = {
        RecipeOwnerKindEnum.PRODUCT.value: Product,
        RecipeOwnerKindEnum.MODIFIER_OPTION.value: ModifierOption,
        RecipeOwnerKindEnum.INVENTORY_ITEM.value: InventoryItem,
    }.get(kind)
    if model is None:
        return str(owner_id)
    name = (
        await db.execute(select(model.name).where(model.id == owner_id))
    ).scalar_one_or_none()
    return name or str(owner_id)


def owner_ref(recipe: Recipe) -> tuple[str, uuid.UUID]:
    """A recipe's (owner_kind, owner_id) from whichever owner FK is set."""
    owner_id = (
        recipe.product_id or recipe.modifier_option_id or recipe.inventory_item_id
    )
    return recipe.owner_kind, owner_id


async def get_recipe(db: AsyncSession, kind: str, owner_id: uuid.UUID) -> Recipe | None:
    column = _owner_column(kind)
    stmt = (
        select(Recipe)
        .where(Recipe.owner_kind == kind, column == owner_id)
        .options(
            selectinload(Recipe.versions)
            .selectinload(RecipeVersion.lines)
            .selectinload(RecipeLine.item)
        )
        # See _load_version: guarantee the item eager load runs for in-session
        # objects so the live ingredient_unit serialises without a lazy load.
        .execution_options(populate_existing=True)
    )
    return (await db.execute(stmt)).scalars().unique().one_or_none()


async def active_version(
    db: AsyncSession, kind: str, owner_id: uuid.UUID
) -> RecipeVersion | None:
    column = _owner_column(kind)
    stmt = (
        select(RecipeVersion)
        .join(Recipe, Recipe.id == RecipeVersion.recipe_id)
        .where(
            Recipe.owner_kind == kind,
            column == owner_id,
            RecipeVersion.status == RecipeVersionStatusEnum.ACTIVE.value,
        )
        .options(selectinload(RecipeVersion.lines).selectinload(RecipeLine.item))
    )
    return (await db.execute(stmt)).scalars().unique().one_or_none()


async def item_produces_something(db: AsyncSession, item_id: uuid.UUID) -> bool:
    """Whether an inventory item is *made* — has an active recipe (v2) or a legacy
    bill of materials. This is the "produces something" test the production order
    uses: only such items can appear as a production line."""
    if (
        await active_version(db, RecipeOwnerKindEnum.INVENTORY_ITEM.value, item_id)
        is not None
    ):
        return True
    legacy = await db.scalar(
        select(func.count())
        .select_from(InventoryItemIngredient)
        .where(InventoryItemIngredient.parent_item_id == item_id)
    )
    return bool(legacy)


async def producible_item_ids(db: AsyncSession) -> set[uuid.UUID]:
    """Every inventory item that produces something — active v2 recipe or legacy
    BOM. The admin transfer/production grid gates its "qty to produce" input on
    membership of this set, so only makeable items are offered."""
    v2 = (
        (
            await db.execute(
                select(Recipe.inventory_item_id)
                .join(RecipeVersion, RecipeVersion.recipe_id == Recipe.id)
                .where(
                    Recipe.owner_kind == RecipeOwnerKindEnum.INVENTORY_ITEM.value,
                    Recipe.inventory_item_id.is_not(None),
                    RecipeVersion.status == RecipeVersionStatusEnum.ACTIVE.value,
                )
            )
        )
        .scalars()
        .all()
    )
    legacy = (
        (await db.execute(select(InventoryItemIngredient.parent_item_id).distinct()))
        .scalars()
        .all()
    )
    return {i for i in v2 if i is not None} | {i for i in legacy if i is not None}


async def item_production_basis(
    db: AsyncSession, item_id: uuid.UUID
) -> tuple[str, Decimal | None]:
    """The recipe basis an inventory item is produced in.

    Returns ``('unit', None)`` or ``('batch', batch_yield)``, read from the item's
    *current active* recipe version — so a later basis or yield change flows into
    the next production order raised, while orders already raised keep the value
    snapshotted onto their lines. A legacy-BOM item (no v2 version) has no basis
    and produces in units.
    """
    version = await active_version(
        db, RecipeOwnerKindEnum.INVENTORY_ITEM.value, item_id
    )
    if (
        version is not None
        and version.basis == RecipeBasisEnum.BATCH.value
        and version.batch_yield
    ):
        return RecipeBasisEnum.BATCH.value, Decimal(str(version.batch_yield))
    return RecipeBasisEnum.UNIT.value, None


async def producible_item_bases(
    db: AsyncSession,
) -> dict[uuid.UUID, tuple[str, Decimal | None]]:
    """``item_id → (basis, batch_yield)`` for every item with an active v2 recipe.

    The admin grid reads this to render the "qty to produce" cell in the item's
    basis (batches vs units) and show the live unit conversion. Items missing from
    the map (legacy BOM, or not producible) are treated as unit basis by callers.
    """
    rows = (
        await db.execute(
            select(
                Recipe.inventory_item_id,
                RecipeVersion.basis,
                RecipeVersion.batch_yield,
            )
            .join(RecipeVersion, RecipeVersion.recipe_id == Recipe.id)
            .where(
                Recipe.owner_kind == RecipeOwnerKindEnum.INVENTORY_ITEM.value,
                Recipe.inventory_item_id.is_not(None),
                RecipeVersion.status == RecipeVersionStatusEnum.ACTIVE.value,
            )
        )
    ).all()
    out: dict[uuid.UUID, tuple[str, Decimal | None]] = {}
    for item_id, basis, batch_yield in rows:
        if item_id is None:
            continue
        if basis == RecipeBasisEnum.BATCH.value and batch_yield:
            out[item_id] = (RecipeBasisEnum.BATCH.value, Decimal(str(batch_yield)))
        else:
            out[item_id] = (RecipeBasisEnum.UNIT.value, None)
    return out


def _normalise_basis(
    basis: str, batch_yield: Decimal | None
) -> tuple[str, Decimal | None]:
    """Validate the batch fields and coerce them to a canonical pair.

    'unit' recipes never carry a yield; 'batch' recipes require a positive one.
    """
    if basis not in (RecipeBasisEnum.UNIT.value, RecipeBasisEnum.BATCH.value):
        raise BadRequestError(f"Unknown recipe basis '{basis}'")
    if basis == RecipeBasisEnum.BATCH.value:
        if batch_yield is None or Decimal(str(batch_yield)) <= 0:
            raise BadRequestError(
                "A batch recipe needs a batch yield greater than zero"
            )
        return basis, Decimal(str(batch_yield))
    return basis, None


async def create_draft(
    db: AsyncSession,
    *,
    kind: str,
    owner_id: uuid.UUID,
    lines: Iterable[RecipeLineInput],
    basis: str = RecipeBasisEnum.UNIT.value,
    batch_yield: Decimal | None = None,
    source: str = "mm",
    source_payload_hash: str | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> RecipeVersion:
    basis, batch_yield = _normalise_basis(basis, batch_yield)
    await _assert_owner_exists(db, kind, owner_id)
    recipe = await get_recipe(db, kind, owner_id)
    if recipe is None:
        kwargs = {"owner_kind": kind, f"{kind}_id": owner_id}
        recipe = Recipe(**kwargs)
        db.add(recipe)
        await db.flush()
        # A newly flushed ORM object has no loaded relationship collection.
        # Reading ``recipe.versions`` here would issue a lazy query, which is
        # illegal from SQLAlchemy's async attribute accessor and raises
        # MissingGreenlet.  There cannot be an existing version on a newly
        # created recipe, so retain that fact instead of querying it.
        recipe_versions: Sequence[RecipeVersion] = ()
    else:
        recipe_versions = recipe.versions

    if source_payload_hash:
        same_snapshot = next(
            (
                version
                for version in recipe_versions
                if version.source == source
                and version.source_payload_hash == source_payload_hash
            ),
            None,
        )
        if (
            same_snapshot is not None
            and same_snapshot.status != RecipeVersionStatusEnum.DRAFT.value
        ):
            return await _load_version(db, same_snapshot.id)

    existing_draft = next(
        (
            version
            for version in recipe_versions
            if version.status == RecipeVersionStatusEnum.DRAFT.value
        ),
        None,
    )
    if existing_draft is None:
        latest = int(
            (
                await db.execute(
                    select(
                        func.coalesce(func.max(RecipeVersion.version_number), 0)
                    ).where(RecipeVersion.recipe_id == recipe.id)
                )
            ).scalar_one()
        )
        existing_draft = RecipeVersion(
            recipe_id=recipe.id,
            version_number=latest + 1,
            status=RecipeVersionStatusEnum.DRAFT.value,
            basis=basis,
            batch_yield=batch_yield,
            source=source,
            source_payload_hash=source_payload_hash,
            source_metadata=source_metadata or {},
        )
        db.add(existing_draft)
        await db.flush()
    else:
        if existing_draft.source != source or (
            source_payload_hash
            and existing_draft.source_payload_hash
            and existing_draft.source_payload_hash != source_payload_hash
        ):
            raise ConflictError(
                "This recipe already has a draft from another edit or import batch"
            )
        await db.execute(
            delete(RecipeLine).where(RecipeLine.recipe_version_id == existing_draft.id)
        )
        existing_draft.basis = basis
        existing_draft.batch_yield = batch_yield
        existing_draft.source = source
        existing_draft.source_payload_hash = source_payload_hash
        existing_draft.source_metadata = source_metadata or {}

    seen: set[uuid.UUID] = set()
    for position, input_line in enumerate(lines):
        if input_line.item_id in seen:
            raise BadRequestError(
                f"Inventory item {input_line.item_id} appears more than once"
            )
        seen.add(input_line.item_id)
        item = await db.get(InventoryItem, input_line.item_id)
        if item is None:
            raise BadRequestError(f"Inventory item {input_line.item_id} not found")
        quantity = Decimal(str(input_line.quantity))
        yield_percentage = Decimal(str(input_line.yield_percentage))
        if quantity <= 0:
            raise BadRequestError("Recipe quantities must be positive")
        if yield_percentage <= 0 or yield_percentage > 1:
            raise BadRequestError(
                "Recipe yield must be greater than zero and at most one"
            )
        db.add(
            RecipeLine(
                recipe_version_id=existing_draft.id,
                item_id=item.id,
                quantity=quantity,
                yield_percentage=yield_percentage,
                inactive_in_order_types=input_line.inactive_in_order_types,
                display_order=input_line.display_order or position,
                source_metadata=input_line.source_metadata,
            )
        )

    await db.flush()
    return await _load_version(db, existing_draft.id)


async def _load_version(db: AsyncSession, version_id: uuid.UUID) -> RecipeVersion:
    stmt = (
        select(RecipeVersion)
        .where(RecipeVersion.id == version_id)
        .options(selectinload(RecipeVersion.lines).selectinload(RecipeLine.item))
        # ``populate_existing`` forces the eager loads to run even when the
        # version and its lines were just created in this session — otherwise
        # the identity map hands back the pending line with its ``item`` (whose
        # ingredient_unit the response reads) unloaded, and serialising it under
        # async raises MissingGreenlet.
        .execution_options(populate_existing=True)
    )
    version = (await db.execute(stmt)).scalars().unique().one_or_none()
    if version is None:
        raise NotFoundError("Recipe version not found")
    return version


async def _inventory_graph(
    db: AsyncSession, candidate: RecipeVersion
) -> dict[uuid.UUID, set[uuid.UUID]]:
    candidate_recipe = await db.get(Recipe, candidate.recipe_id)
    stmt = (
        select(Recipe, RecipeVersion)
        .join(RecipeVersion, RecipeVersion.recipe_id == Recipe.id)
        .where(
            Recipe.owner_kind == RecipeOwnerKindEnum.INVENTORY_ITEM.value,
            RecipeVersion.status == RecipeVersionStatusEnum.ACTIVE.value,
        )
        .options(selectinload(RecipeVersion.lines).selectinload(RecipeLine.item))
    )
    graph: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for recipe, version in (await db.execute(stmt)).unique().all():
        if candidate_recipe and recipe.id == candidate_recipe.id:
            continue
        assert recipe.inventory_item_id is not None
        graph[recipe.inventory_item_id].update(line.item_id for line in version.lines)
    if candidate_recipe and candidate_recipe.inventory_item_id is not None:
        graph[candidate_recipe.inventory_item_id] = {
            line.item_id for line in candidate.lines
        }
    return graph


def _assert_acyclic(graph: dict[uuid.UUID, set[uuid.UUID]]) -> None:
    visiting: set[uuid.UUID] = set()
    visited: set[uuid.UUID] = set()

    def visit(node: uuid.UUID, path: list[uuid.UUID]) -> None:
        if node in visiting:
            cycle = path[path.index(node) :] + [node]
            raise ConflictError(
                "Recipe cycle detected: " + " -> ".join(str(value) for value in cycle)
            )
        if node in visited:
            return
        visiting.add(node)
        for child in graph.get(node, set()):
            if child in graph:
                visit(child, [*path, child])
        visiting.remove(node)
        visited.add(node)

    for root in graph:
        visit(root, [root])


async def activate(
    db: AsyncSession,
    *,
    version_id: uuid.UUID,
    user_id: uuid.UUID,
) -> RecipeVersion:
    candidate = await _load_version(db, version_id)
    await db.execute(
        select(Recipe).where(Recipe.id == candidate.recipe_id).with_for_update()
    )
    candidate = await _load_version(db, version_id)
    if candidate.status != RecipeVersionStatusEnum.DRAFT.value:
        raise ConflictError("Only a draft recipe version can be activated")
    await _validate_candidate(db, candidate)

    current = (
        (
            await db.execute(
                select(RecipeVersion).where(
                    RecipeVersion.recipe_id == candidate.recipe_id,
                    RecipeVersion.status == RecipeVersionStatusEnum.ACTIVE.value,
                )
            )
        )
        .scalars()
        .one_or_none()
    )
    now = utcnow()
    if current is not None:
        current.status = RecipeVersionStatusEnum.RETIRED.value
        current.retired_at = now
        await db.flush()
    candidate.status = RecipeVersionStatusEnum.ACTIVE.value
    candidate.activated_at = now
    candidate.activated_by = user_id
    await db.flush()
    # Wake pending missing-recipe events only when the active graph changes.  The
    # update is transactional with activation, so a sweeper can never observe a
    # generation whose recipe version is not visible yet.
    await _bump_catalog_generation(db)
    # Auto off-sale (experimental): the produced goods either version reaches
    # are re-evaluated at every enabled branch, so an owner whose new recipe
    # dropped one is released. A no-op while no branch has the flag on.
    from app.services.inventory import auto_availability_service

    await auto_availability_service.mark_recipe_change(db, [current, candidate])
    return await _load_version(db, candidate.id)


async def _validate_candidate(db: AsyncSession, candidate: RecipeVersion) -> Recipe:
    """Validate a draft without publishing it or changing recipe history."""
    if not candidate.lines:
        raise BadRequestError("A recipe must contain at least one inventory item")
    _normalise_basis(candidate.basis, candidate.batch_yield)

    candidate_recipe = await db.get(Recipe, candidate.recipe_id)
    if candidate_recipe is None:
        raise NotFoundError("Recipe not found")
    for line in candidate.lines:
        item = await db.get(InventoryItem, line.item_id)
        if item is None:
            raise BadRequestError(f"Inventory item {line.item_id} not found")
        if item.tracking_mode == InventoryTrackingModeEnum.PHANTOM.value:
            nested = await active_version(
                db, RecipeOwnerKindEnum.INVENTORY_ITEM.value, item.id
            )
            is_self_candidate = bool(candidate_recipe.inventory_item_id == item.id)
            if nested is None and not is_self_candidate:
                raise ConflictError(f"Phantom item {item.name} has no active recipe")

    _assert_acyclic(await _inventory_graph(db, candidate))
    return candidate_recipe


async def preview_version(
    db: AsyncSession,
    *,
    version_id: uuid.UUID,
    multiplier: Decimal = Decimal("1"),
    order_type: str | None = None,
) -> tuple[dict[uuid.UUID, ExpandedLine], set[uuid.UUID]]:
    """Expand a validated draft using active dependencies, without activation."""
    candidate = await _load_version(db, version_id)
    if candidate.status != RecipeVersionStatusEnum.DRAFT.value:
        raise ConflictError("Only a draft recipe version needs a preview")
    recipe = await _validate_candidate(db, candidate)
    owner_id = (
        recipe.product_id or recipe.modifier_option_id or recipe.inventory_item_id
    )
    if owner_id is None:
        raise ConflictError("Recipe has no owner")

    catalog = await load_active_catalog(db)
    catalog.versions[(recipe.owner_kind, owner_id)] = candidate
    missing_ids = {line.item_id for line in candidate.lines} - set(catalog.items)
    if missing_ids:
        for item in (
            await db.execute(
                select(InventoryItem).where(InventoryItem.id.in_(missing_ids))
            )
        ).scalars():
            catalog.items[item.id] = item
    return await expand_owner(
        db,
        kind=recipe.owner_kind,
        owner_id=owner_id,
        multiplier=multiplier,
        order_type=order_type,
        catalog=catalog,
    )


async def draft_and_activate(
    db: AsyncSession,
    *,
    kind: str,
    owner_id: uuid.UUID,
    lines: Iterable[RecipeLineInput],
    user_id: uuid.UUID,
) -> RecipeVersion:
    draft = await create_draft(db, kind=kind, owner_id=owner_id, lines=lines)
    return await activate(db, version_id=draft.id, user_id=user_id)


def _walk_version(
    catalog: ActiveRecipeCatalog,
    totals: dict[uuid.UUID, ExpandedLine],
    used_versions: set[uuid.UUID],
    *,
    owner_kind: str,
    owner_id: uuid.UUID,
    version_id: uuid.UUID | None,
    basis: str,
    batch_yield: Decimal | None,
    lines: Iterable,
    gross_scale: Decimal,
    net_scale: Decimal,
    path: list[dict[str, str]],
    ancestry: set[uuid.UUID],
    order_type: str | None,
) -> None:
    """Fold one version's lines into *totals* — the one recipe expansion rule.

    A batch recipe's lines make ``batch_yield`` owner units, so demand is divided
    by the yield before its lines are drawn; that composes through nested
    phantom sub-recipes (each dividing by its own yield). A line's
    ``yield_percentage`` grosses its quantity up for planned waste. Output is in
    each leaf ingredient's ingredient unit.
    """
    if version_id is not None:
        used_versions.add(version_id)
    if basis == RecipeBasisEnum.BATCH.value and batch_yield:
        version_scale = Decimal(str(batch_yield))
        gross_scale = gross_scale / version_scale
        net_scale = net_scale / version_scale
    for recipe_line in sorted(lines, key=lambda value: value.display_order or 0):
        if order_type and order_type in (recipe_line.inactive_in_order_types or []):
            continue
        item = catalog.items.get(recipe_line.item_id)
        if item is None:
            raise NotFoundError(f"Inventory item {recipe_line.item_id} not found")
        recipe_quantity = Decimal(str(recipe_line.quantity))
        net = net_scale * recipe_quantity
        gross = (
            gross_scale
            * recipe_quantity
            / Decimal(str(recipe_line.yield_percentage or 1))
        )
        step = {
            "owner_kind": owner_kind,
            "owner_id": str(owner_id),
            "recipe_version_id": str(version_id) if version_id else "",
            "item_id": str(item.id),
        }
        next_path = [*path, step]
        if item.tracking_mode == InventoryTrackingModeEnum.PHANTOM.value:
            if item.id in ancestry:
                raise ConflictError("Recipe cycle encountered during expansion")
            child = catalog.versions.get(
                (RecipeOwnerKindEnum.INVENTORY_ITEM.value, item.id)
            )
            if child is None:
                raise NotFoundError(f"No active recipe for inventory item {item.id}")
            _walk_version(
                catalog,
                totals,
                used_versions,
                owner_kind=RecipeOwnerKindEnum.INVENTORY_ITEM.value,
                owner_id=item.id,
                version_id=child.id,
                basis=child.basis,
                batch_yield=child.batch_yield,
                lines=child.lines,
                gross_scale=gross,
                net_scale=net,
                path=next_path,
                ancestry={*ancestry, item.id},
                order_type=order_type,
            )
            continue
        aggregate = totals.setdefault(
            item.id, ExpandedLine(item_id=item.id, quantity=Decimal("0"))
        )
        aggregate.quantity += gross
        aggregate.planned_waste += gross - net
        if version_id is not None:
            aggregate.recipe_version_ids.add(version_id)
        aggregate.paths.append(next_path)


def _quantized(totals: dict[uuid.UUID, ExpandedLine]) -> dict[uuid.UUID, ExpandedLine]:
    for value in totals.values():
        value.quantity = quantize_quantity(value.quantity)
        value.planned_waste = quantize_quantity(value.planned_waste)
    return totals


async def expand_owner(
    db: AsyncSession,
    *,
    kind: str,
    owner_id: uuid.UUID,
    multiplier: Decimal = Decimal("1"),
    order_type: str | None = None,
    catalog: ActiveRecipeCatalog | None = None,
) -> tuple[dict[uuid.UUID, ExpandedLine], set[uuid.UUID]]:
    catalog = catalog or await load_active_catalog(db)
    version = catalog.versions.get((kind, owner_id))
    if version is None:
        raise NotFoundError(f"No active recipe for {kind.replace('_', ' ')} {owner_id}")
    totals: dict[uuid.UUID, ExpandedLine] = {}
    used_versions: set[uuid.UUID] = set()
    root_scale = Decimal(str(multiplier))
    _walk_version(
        catalog,
        totals,
        used_versions,
        owner_kind=kind,
        owner_id=owner_id,
        version_id=version.id,
        basis=version.basis,
        batch_yield=version.batch_yield,
        lines=version.lines,
        gross_scale=root_scale,
        net_scale=root_scale,
        path=[],
        ancestry=set(),
        order_type=order_type,
    )
    return _quantized(totals), used_versions


def expand_lines(
    catalog: ActiveRecipeCatalog,
    *,
    owner_kind: str,
    owner_id: uuid.UUID | None,
    basis: str,
    batch_yield: Decimal | None,
    lines: Iterable,
) -> dict[uuid.UUID, ExpandedLine]:
    """Expand lines that are not (or not yet) the active version — a draft, or
    the editor's unsaved lines — to one owner unit, by the same rule."""
    totals: dict[uuid.UUID, ExpandedLine] = {}
    _walk_version(
        catalog,
        totals,
        set(),
        owner_kind=owner_kind,
        owner_id=owner_id or uuid.UUID(int=0),
        version_id=None,
        basis=basis,
        batch_yield=batch_yield,
        lines=lines,
        gross_scale=Decimal("1"),
        net_scale=Decimal("1"),
        path=[],
        ancestry={owner_id} if owner_id else set(),
        order_type=None,
    )
    return _quantized(totals)


async def ingredient_storage_costs(
    db: AsyncSession,
    item_ids: Iterable[uuid.UUID],
    *,
    warehouse_ids: list[uuid.UUID] | None = None,
) -> dict[uuid.UUID, Decimal]:
    """Each ingredient's current FIFO cost per storage unit — at the given
    warehouses (one branch), or blended across the estate when None. Read-only."""
    from app.services.inventory import cost_layer_service

    ids = list(set(item_ids))
    if warehouse_ids is None:
        return await cost_layer_service.item_average_costs(db, ids)
    return await cost_layer_service.warehouse_average_costs(db, ids, warehouse_ids)


def expansion_cost(
    expanded: dict[uuid.UUID, ExpandedLine],
    items: dict[uuid.UUID, InventoryItem],
    storage_costs: dict[uuid.UUID, Decimal],
) -> Decimal:
    """Σ gross ingredient quantity × that ingredient's cost per ingredient unit —
    exactly what ``produce`` books as a batch's input cost."""
    from app.services.inventory import inventory_service

    total = Decimal("0")
    for line in expanded.values():
        ingredient = items.get(line.item_id)
        if ingredient is None:
            raise NotFoundError(f"Inventory item {line.item_id} not found")
        ingredient_cost = inventory_service.canonical_cost_for_unit(
            ingredient, storage_costs.get(line.item_id, Decimal("0")), "ingredient"
        )
        total += Decimal(str(line.quantity)) * ingredient_cost
    return quantize_cost(total)


async def owner_recipe_unit_cost(
    db: AsyncSession,
    *,
    kind: str,
    owner_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = None,
    warehouse_ids: list[uuid.UUID] | None = None,
    catalog: ActiveRecipeCatalog | None = None,
) -> Decimal | None:
    """The current cost of **one owner unit** from its active recipe.

    The recipe is expanded to its leaf ingredients — through nested phantom
    sub-recipes, batch yield and per-line waste — and each leaf is priced at its
    FIFO average cost: at one warehouse (``warehouse_id``) or one branch
    (``warehouse_ids``) when given, the basis ``produce`` books there; blended
    across the estate otherwise (a catalogue-level figure).

    Returns ``None`` when the owner has no active recipe (nothing to cost from).
    """
    catalog = catalog or await load_active_catalog(db)
    try:
        expanded, _ = await expand_owner(
            db, kind=kind, owner_id=owner_id, catalog=catalog
        )
    except NotFoundError:
        return None
    if not expanded:
        return Decimal("0")
    if warehouse_id is not None:
        warehouse_ids = [warehouse_id]
    costs = await ingredient_storage_costs(
        db, expanded.keys(), warehouse_ids=warehouse_ids
    )
    return expansion_cost(expanded, catalog.items, costs)


async def quote_lines(
    db: AsyncSession,
    *,
    owner_kind: str,
    owner_id: uuid.UUID | None,
    basis: str,
    batch_yield: Decimal | None,
    lines: list,
    branch_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Cost a recipe's lines exactly as production would book them — the figure
    the editor shows while a recipe is being written, so the browser never runs
    a cost formula of its own. Per line (as authored) and per owner unit."""
    from app.services.inventory import inventory_service

    catalog = await load_active_catalog(db)
    missing = {line.item_id for line in lines} - set(catalog.items)
    if missing:
        for item in (
            await db.execute(select(InventoryItem).where(InventoryItem.id.in_(missing)))
        ).scalars():
            catalog.items[item.id] = item
    per_line = [
        expand_lines(
            catalog,
            owner_kind=owner_kind,
            owner_id=owner_id,
            basis=RecipeBasisEnum.UNIT.value,
            batch_yield=None,
            lines=[line],
        )
        for line in lines
    ]
    warehouse_ids = (
        await inventory_service.branch_warehouse_ids(db, branch_id)
        if branch_id is not None
        else None
    )
    costs = await ingredient_storage_costs(
        db,
        {item_id for expanded in per_line for item_id in expanded},
        warehouse_ids=warehouse_ids,
    )
    quoted = []
    authored_total = Decimal("0")
    for line, expanded in zip(lines, per_line):
        line_cost = expansion_cost(expanded, catalog.items, costs)
        authored_total += line_cost
        quantity = Decimal(str(line.quantity))
        quoted.append(
            {
                "item_id": line.item_id,
                "unit_cost": quantize_cost(line_cost / quantity)
                if quantity
                else Decimal("0"),
                "line_cost": line_cost,
            }
        )
    is_batch = basis == RecipeBasisEnum.BATCH.value and batch_yield
    return {
        "lines": quoted,
        "unit_cost": quantize_cost(
            authored_total / Decimal(str(batch_yield)) if is_batch else authored_total
        ),
        "batch_cost": quantize_cost(authored_total) if is_batch else None,
    }


async def recipe_unit_cost(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = None,
    catalog: ActiveRecipeCatalog | None = None,
) -> Decimal | None:
    """Live per-ingredient-unit cost of a made inventory item (see
    :func:`owner_recipe_unit_cost`). Pass ``warehouse_id`` to price ingredients at
    that branch's levels. ``None`` when it has no active recipe."""
    return await owner_recipe_unit_cost(
        db,
        kind=RecipeOwnerKindEnum.INVENTORY_ITEM.value,
        owner_id=item_id,
        warehouse_id=warehouse_id,
        catalog=catalog,
    )


async def product_recipe_unit_cost(
    db: AsyncSession,
    *,
    product_id: uuid.UUID,
    catalog: ActiveRecipeCatalog | None = None,
) -> Decimal | None:
    """Live cost of one unit of a product, from its active recipe's ingredient FIFO
    cost. ``None`` when the product has no active recipe. This replaces the stale,
    CSV-imported ``Product.cost`` column, which was dropped in favour of costing a
    product live the same way its inventory-item cousins are."""
    return await owner_recipe_unit_cost(
        db,
        kind=RecipeOwnerKindEnum.PRODUCT.value,
        owner_id=product_id,
        catalog=catalog,
    )


async def reset_item_cost_from_recipe(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    user: User,
    notes: str | None = None,
) -> dict[str, Any]:
    """Revalue every on-hand unit of a made item to its current recipe cost.

    Each branch's stock is revalued to the recipe cost computed from **that
    branch's own** ingredient FIFO costs — the same per-branch basis a production
    run books there — through the standard cost-adjustment path
    (``inventory_service.adjust_cost``: a COST_ADJUSTMENT that rescales the FIFO
    layers, never an edit of an immutable ledger line). A branch whose recipe
    prices to zero (its ingredients are not costed there) is left untouched rather
    than zeroed, and each branch is savepointed so one bad level cannot abort the
    rest. Only active (non-deleted) warehouses are touched.
    """
    from app.services.inventory import inventory_service

    item = await db.get(InventoryItem, item_id)
    if item is None:
        raise NotFoundError("Inventory item not found")
    # Establish the item even has an active recipe before walking its levels, so
    # "no recipe" and "recipe prices to zero everywhere" give distinct errors.
    if await recipe_unit_cost(db, item_id=item_id) is None:
        raise BadRequestError("This item has no active recipe to cost from")

    factor = Decimal(str(item.storage_to_ingredient_factor or 1))
    rows = (
        await db.execute(
            select(InventoryLevel, Warehouse, Branch)
            .join(Warehouse, Warehouse.id == InventoryLevel.warehouse_id)
            .join(Branch, Branch.id == Warehouse.branch_id)
            .where(
                InventoryLevel.item_id == item_id,
                Warehouse.deleted_at.is_(None),
                Warehouse.is_active.is_(True),
            )
        )
    ).all()

    adjustments: list[dict[str, Any]] = []
    skipped = 0
    for _level, warehouse, branch in rows:
        # Cost from this branch's own ingredient levels — estate-wide blending
        # would misstate a branch that sources cheaper/dearer than another.
        per_ingredient_unit = await recipe_unit_cost(
            db, item_id=item_id, warehouse_id=warehouse.id
        )
        if not per_ingredient_unit or per_ingredient_unit <= 0:
            skipped += 1
            continue
        # Levels are valued per storage unit; recipe cost is per ingredient unit,
        # so scale up by the item's factor (storage = ingredient × factor).
        storage_cost = quantize_cost(per_ingredient_unit * factor)
        try:
            async with db.begin_nested():
                result = await inventory_service.adjust_cost(
                    db,
                    branch=branch,
                    item_id=item_id,
                    warehouse_id=warehouse.id,
                    new_average_cost=storage_cost,
                    user=user,
                    notes=notes or "Reset from current recipe cost",
                )
            adjustments.append(result)
        except AppError as exc:
            skipped += 1
            logger.info(
                "reset-from-recipe: skipped %s @ %s — %s", item_id, branch.name, exc
            )

    if not adjustments:
        raise BadRequestError(
            "Nothing to revalue: this item holds no stock at a branch whose recipe "
            "ingredients are costed. Cost the ingredients first."
        )
    return {
        "item_id": str(item_id),
        "levels_adjusted": len(adjustments),
        "levels_skipped": skipped,
        "adjustments": adjustments,
    }


async def load_active_catalog(db: AsyncSession) -> ActiveRecipeCatalog:
    """Load active versions and all referenced items without recursive N+1 IO."""
    stmt = (
        select(Recipe, RecipeVersion)
        .join(RecipeVersion, RecipeVersion.recipe_id == Recipe.id)
        .where(RecipeVersion.status == RecipeVersionStatusEnum.ACTIVE.value)
        .options(selectinload(RecipeVersion.lines).selectinload(RecipeLine.item))
    )
    rows = (await db.execute(stmt)).unique().all()
    versions: dict[tuple[str, uuid.UUID], RecipeVersion] = {}
    item_ids: set[uuid.UUID] = set()
    for recipe, version in rows:
        owner_id = (
            recipe.product_id or recipe.modifier_option_id or recipe.inventory_item_id
        )
        if owner_id is not None:
            versions[(recipe.owner_kind, owner_id)] = version
        item_ids.update(line.item_id for line in version.lines)
    items = (
        {
            item.id: item
            for item in (
                await db.execute(
                    select(InventoryItem).where(InventoryItem.id.in_(item_ids))
                )
            )
            .scalars()
            .all()
        }
        if item_ids
        else {}
    )
    return ActiveRecipeCatalog(versions=versions, items=items)


async def snapshot_order(
    db: AsyncSession, order: Order, *, catalog: ActiveRecipeCatalog | None = None
) -> tuple[dict[str, Any], list[str]]:
    """`catalog` lets a caller that snapshots MANY orders in one pass (the pending
    sweeper) load the active recipe graph ONCE and reuse it, instead of paying a
    full `load_active_catalog` per order — the per-event reload is what pushed a
    branch's backlog past the sweep budget and rolled the whole branch back every
    tick. A per-request caller (accept/reconsume) omits it and loads fresh."""
    totals: dict[uuid.UUID, ExpandedLine] = {}
    version_ids: set[uuid.UUID] = set()
    warnings: list[str] = []
    items = list(
        (await db.execute(select(OrderItem).where(OrderItem.order_id == order.id)))
        .scalars()
        .all()
    )

    # Products that draw NO tracked inventory (`consumes_stock=False`): their recipe
    # is not expected and not expanded, so a line for one raises no `missing_recipe`
    # warning and the order can close as a clean no-movement. Modifier options on
    # such a line still expand — a non-consuming base ("Fudge Brownies") whose
    # consumption lives on its quantity option is exactly why the two are separate.
    line_product_ids = {
        line.product_id for line in items if line.product_id is not None
    }
    non_consuming: set[uuid.UUID] = set()
    if line_product_ids:
        non_consuming = set(
            (
                await db.execute(
                    select(Product.id).where(
                        Product.id.in_(line_product_ids),
                        Product.consumes_stock.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )

    catalog = catalog or await load_active_catalog(db)

    async def merge(
        kind: str, owner_id: uuid.UUID, multiplier: Decimal, label: str
    ) -> None:
        try:
            expanded, used = await expand_owner(
                db,
                kind=kind,
                owner_id=owner_id,
                multiplier=multiplier,
                order_type=order.order_type,
                catalog=catalog,
            )
        except NotFoundError:
            warnings.append(f"Missing active recipe for {label}")
            return
        version_ids.update(used)
        for item_id, contribution in expanded.items():
            aggregate = totals.setdefault(
                item_id, ExpandedLine(item_id=item_id, quantity=Decimal("0"))
            )
            aggregate.quantity += contribution.quantity
            aggregate.planned_waste += contribution.planned_waste
            aggregate.recipe_version_ids.update(contribution.recipe_version_ids)
            aggregate.paths.extend(contribution.paths)

    for line in items:
        if line.status == "void" or line.product_id is None:
            continue
        billable = Decimal(str(max(line.quantity - (line.returned_quantity or 0), 0)))
        if billable <= 0:
            continue
        if line.product_id not in non_consuming:
            await merge(
                RecipeOwnerKindEnum.PRODUCT.value,
                line.product_id,
                billable,
                f"product {line.product_id}",
            )
        for option in line.selected_options_snapshot or []:
            raw_id = option.get("modifier_option_id")
            if not raw_id:
                continue
            try:
                option_id = uuid.UUID(str(raw_id))
            except (TypeError, ValueError):
                warnings.append(f"Invalid modifier option id {raw_id!r}")
                continue
            option_quantity = Decimal(str(option.get("quantity", 1) or 1))
            await merge(
                RecipeOwnerKindEnum.MODIFIER_OPTION.value,
                option_id,
                billable * option_quantity,
                f"modifier option {option_id}",
            )

    return (
        {
            "order_id": str(order.id),
            "order_number": order.order_number,
            "recipe_version_ids": sorted(str(value) for value in version_ids),
            "lines": [totals[key].as_snapshot() for key in sorted(totals, key=str)],
        },
        warnings,
    )


async def branch_menu_recipe_gaps(
    db: AsyncSession, branch_id: uuid.UUID
) -> dict[str, Any]:
    """Products and modifier options a branch can sell that have no active recipe.

    A pre-go-live check: a sellable item without an active recipe expands to no
    consumption, so its sales silently move no stock and the ledger drifts from the
    shelf from day one. This walks the branch's live menu tree and lists what would
    be missed, without changing anything — the operator activates the missing
    recipes before enabling inventory.
    """
    # Imported here (not at module top) to keep the recipe service from importing
    # the catalog menu service at module load, where it would risk a cycle.
    from app.services.catalog import menu_group_service

    product_ids = set(
        (
            await db.execute(
                select(Product.id).where(
                    Product.id.in_(
                        menu_group_service.visible_product_ids_subquery(
                            branch_id=branch_id
                        )
                    ),
                    Product.is_active == True,  # noqa: E712
                )
            )
        )
        .scalars()
        .all()
    )
    products_with_recipe = set(
        (
            await db.execute(
                select(Recipe.product_id)
                .join(RecipeVersion, RecipeVersion.recipe_id == Recipe.id)
                .where(
                    Recipe.owner_kind == RecipeOwnerKindEnum.PRODUCT.value,
                    Recipe.product_id.in_(product_ids),
                    RecipeVersion.status == RecipeVersionStatusEnum.ACTIVE.value,
                )
            )
        )
        .scalars()
        .all()
    )
    option_ids = set(
        (
            await db.execute(
                select(ModifierOption.id)
                .join(Modifier, Modifier.id == ModifierOption.modifier_id)
                .join(ProductModifier, ProductModifier.modifier_id == Modifier.id)
                .where(
                    ProductModifier.product_id.in_(product_ids),
                    ModifierOption.is_active == True,  # noqa: E712
                    Modifier.is_active == True,  # noqa: E712
                )
            )
        )
        .scalars()
        .all()
    )
    options_with_recipe = set(
        (
            await db.execute(
                select(Recipe.modifier_option_id)
                .join(RecipeVersion, RecipeVersion.recipe_id == Recipe.id)
                .where(
                    Recipe.owner_kind == RecipeOwnerKindEnum.MODIFIER_OPTION.value,
                    Recipe.modifier_option_id.in_(option_ids),
                    RecipeVersion.status == RecipeVersionStatusEnum.ACTIVE.value,
                )
            )
        )
        .scalars()
        .all()
    )
    products_missing = sorted(str(pid) for pid in product_ids - products_with_recipe)
    options_missing = sorted(str(oid) for oid in option_ids - options_with_recipe)
    return {
        "branch_id": str(branch_id),
        "ready": not products_missing and not options_missing,
        "products_missing_recipe": products_missing,
        "modifier_options_missing_recipe": options_missing,
    }
