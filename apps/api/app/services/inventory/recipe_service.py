"""Versioned recipe editing, validation, recursive expansion and snapshots.

Recipe lines can only name inventory items. Expansion stops at a stocked item
and walks through a phantom item, which is the boundary that prevents a finished
good's raw materials being consumed once at production and again at sale.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.money import quantity as quantize_quantity
from app.models.base import utcnow
from app.models.inventory import InventoryItem
from app.models.inventory_v2 import (
    InventoryTrackingModeEnum,
    Recipe,
    RecipeLine,
    RecipeOwnerKindEnum,
    RecipeVersion,
    RecipeVersionStatusEnum,
)
from app.models.modifier import ModifierOption
from app.models.order import Order, OrderItem
from app.models.product import Product


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


async def get_recipe(db: AsyncSession, kind: str, owner_id: uuid.UUID) -> Recipe | None:
    column = _owner_column(kind)
    stmt = (
        select(Recipe)
        .where(Recipe.owner_kind == kind, column == owner_id)
        .options(selectinload(Recipe.versions).selectinload(RecipeVersion.lines))
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
        .options(selectinload(RecipeVersion.lines))
    )
    return (await db.execute(stmt)).scalars().unique().one_or_none()


async def create_draft(
    db: AsyncSession,
    *,
    kind: str,
    owner_id: uuid.UUID,
    lines: Iterable[RecipeLineInput],
    source: str = "mm",
    source_payload_hash: str | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> RecipeVersion:
    await _assert_owner_exists(db, kind, owner_id)
    recipe = await get_recipe(db, kind, owner_id)
    if recipe is None:
        kwargs = {"owner_kind": kind, f"{kind}_id": owner_id}
        recipe = Recipe(**kwargs)
        db.add(recipe)
        await db.flush()

    if source_payload_hash:
        same_snapshot = next(
            (
                version
                for version in recipe.versions
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
            for version in recipe.versions
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
                ingredient_unit=item.ingredient_unit,
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
        .options(selectinload(RecipeVersion.lines))
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
        .options(selectinload(RecipeVersion.lines))
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
    return await _load_version(db, candidate.id)


async def _validate_candidate(db: AsyncSession, candidate: RecipeVersion) -> Recipe:
    """Validate a draft without publishing it or changing recipe history."""
    if not candidate.lines:
        raise BadRequestError("A recipe must contain at least one inventory item")

    candidate_recipe = await db.get(Recipe, candidate.recipe_id)
    if candidate_recipe is None:
        raise NotFoundError("Recipe not found")
    for line in candidate.lines:
        item = await db.get(InventoryItem, line.item_id)
        if item is None:
            raise BadRequestError(f"Inventory item {line.item_id} not found")
        if item.ingredient_unit != line.ingredient_unit:
            raise ConflictError(
                f"{item.name} now uses {item.ingredient_unit}; recreate the draft with current units"
            )
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
    totals: dict[uuid.UUID, ExpandedLine] = {}
    used_versions: set[uuid.UUID] = set()

    async def walk(
        owner_kind: str,
        current_owner_id: uuid.UUID,
        gross_scale: Decimal,
        net_scale: Decimal,
        path: list[dict[str, str]],
        ancestry: set[uuid.UUID],
    ) -> None:
        version = catalog.versions.get((owner_kind, current_owner_id))
        if version is None:
            raise NotFoundError(
                f"No active recipe for {owner_kind.replace('_', ' ')} {current_owner_id}"
            )
        used_versions.add(version.id)
        for recipe_line in sorted(version.lines, key=lambda value: value.display_order):
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
                "owner_id": str(current_owner_id),
                "recipe_version_id": str(version.id),
                "item_id": str(item.id),
            }
            next_path = [*path, step]
            if item.tracking_mode == InventoryTrackingModeEnum.PHANTOM.value:
                if item.id in ancestry:
                    raise ConflictError("Recipe cycle encountered during expansion")
                await walk(
                    RecipeOwnerKindEnum.INVENTORY_ITEM.value,
                    item.id,
                    gross,
                    net,
                    next_path,
                    {*ancestry, item.id},
                )
                continue
            aggregate = totals.setdefault(
                item.id, ExpandedLine(item_id=item.id, quantity=Decimal("0"))
            )
            aggregate.quantity += gross
            aggregate.planned_waste += gross - net
            aggregate.recipe_version_ids.add(version.id)
            aggregate.paths.append(next_path)

    root_scale = Decimal(str(multiplier))
    await walk(kind, owner_id, root_scale, root_scale, [], set())
    for value in totals.values():
        value.quantity = quantize_quantity(value.quantity)
        value.planned_waste = quantize_quantity(value.planned_waste)
    return totals, used_versions


async def load_active_catalog(db: AsyncSession) -> ActiveRecipeCatalog:
    """Load active versions and all referenced items without recursive N+1 IO."""
    stmt = (
        select(Recipe, RecipeVersion)
        .join(RecipeVersion, RecipeVersion.recipe_id == Recipe.id)
        .where(RecipeVersion.status == RecipeVersionStatusEnum.ACTIVE.value)
        .options(selectinload(RecipeVersion.lines))
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
    db: AsyncSession, order: Order
) -> tuple[dict[str, Any], list[str]]:
    totals: dict[uuid.UUID, ExpandedLine] = {}
    version_ids: set[uuid.UUID] = set()
    warnings: list[str] = []
    items = list(
        (await db.execute(select(OrderItem).where(OrderItem.order_id == order.id)))
        .scalars()
        .all()
    )

    catalog = await load_active_catalog(db)

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
