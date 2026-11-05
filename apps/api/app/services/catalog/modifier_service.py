from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.schemas.modifier import (
    ModifierCreate,
    ModifierOptionCreate,
    ModifierOptionUpdate,
    ModifierResponse,
    ModifierUpdate,
)

__all__ = [
    "add_option",
    "create",
    "delete",
    "delete_option",
    "get_all",
    "get_by_id",
    "update",
    "update_option",
]


def _modifier_load_options():
    return [selectinload(Modifier.options)]


async def get_all(
    db: AsyncSession, include_inactive: bool = False
) -> list[ModifierResponse]:
    stmt = select(Modifier).options(*_modifier_load_options()).order_by(Modifier.name)
    if not include_inactive:
        stmt = stmt.where(Modifier.is_active == True)  # noqa: E712
    result = await db.execute(stmt)
    modifiers = result.scalars().unique().all()
    return [ModifierResponse.model_validate(m) for m in modifiers]


async def get_by_id(db: AsyncSession, modifier_id: uuid.UUID) -> ModifierResponse:
    stmt = (
        select(Modifier)
        .options(*_modifier_load_options())
        .where(Modifier.id == modifier_id)
    )
    result = await db.execute(stmt)
    modifier = result.scalar_one_or_none()
    if not modifier:
        raise NotFoundError(f"Modifier '{modifier_id}' not found")
    return ModifierResponse.model_validate(modifier)


async def create(db: AsyncSession, data: ModifierCreate) -> ModifierResponse:
    existing = await db.execute(
        select(Modifier).where(Modifier.reference == data.reference)
    )
    if existing.scalar_one_or_none():
        raise ConflictError(
            f"Modifier with reference '{data.reference}' already exists"
        )

    modifier = Modifier(**data.model_dump())
    db.add(modifier)
    await db.flush()

    stmt = (
        select(Modifier)
        .options(*_modifier_load_options())
        .where(Modifier.id == modifier.id)
    )
    result = await db.execute(stmt)
    modifier = result.scalar_one()
    return ModifierResponse.model_validate(modifier)


async def update(
    db: AsyncSession, modifier_id: uuid.UUID, data: ModifierUpdate
) -> ModifierResponse:
    stmt = (
        select(Modifier)
        .options(*_modifier_load_options())
        .where(Modifier.id == modifier_id)
    )
    result = await db.execute(stmt)
    modifier = result.scalar_one_or_none()
    if not modifier:
        raise NotFoundError(f"Modifier '{modifier_id}' not found")

    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(modifier, key, val)

    await db.flush()
    await db.refresh(modifier)

    stmt = (
        select(Modifier)
        .options(*_modifier_load_options())
        .where(Modifier.id == modifier.id)
    )
    result = await db.execute(stmt)
    modifier = result.scalar_one()
    return ModifierResponse.model_validate(modifier)


async def delete(db: AsyncSession, modifier_id: uuid.UUID) -> None:
    result = await db.execute(select(Modifier).where(Modifier.id == modifier_id))
    modifier = result.scalar_one_or_none()
    if not modifier:
        raise NotFoundError(f"Modifier '{modifier_id}' not found")
    modifier.is_active = False
    await db.flush()


async def _enqueue_products_using(db: AsyncSession, modifier_id: uuid.UUID) -> None:
    """An option's price or availability is part of every product carrying its
    modifier, so a change to one is a change those products' integrators must
    hear about — the same outbox a product save writes to. `sync_product` skips
    anything not on the integrator menu, so the queue needs no filtering here."""
    from app.services.aggregators import catalog_sync

    product_ids = (
        (
            await db.execute(
                select(ProductModifier.product_id).where(
                    ProductModifier.modifier_id == modifier_id
                )
            )
        )
        .scalars()
        .all()
    )
    for product_id in sorted(set(product_ids)):
        try:
            await catalog_sync.enqueue_product_sync(
                db, product_id=product_id, reason="option updated"
            )
        except Exception:  # noqa: BLE001 — a queue hiccup must not fail the save
            pass


async def add_option(
    db: AsyncSession, modifier_id: uuid.UUID, data: ModifierOptionCreate
) -> ModifierResponse:
    result = await db.execute(select(Modifier).where(Modifier.id == modifier_id))
    modifier = result.scalar_one_or_none()
    if not modifier:
        raise NotFoundError(f"Modifier '{modifier_id}' not found")

    sku_check = await db.execute(
        select(ModifierOption).where(ModifierOption.sku == data.sku)
    )
    if sku_check.scalar_one_or_none():
        raise ConflictError(f"Modifier option SKU '{data.sku}' already exists")

    option = ModifierOption(modifier_id=modifier_id, **data.model_dump())
    db.add(option)
    await db.flush()
    await _enqueue_products_using(db, modifier_id)

    stmt = (
        select(Modifier)
        .options(*_modifier_load_options())
        .where(Modifier.id == modifier_id)
    )
    result = await db.execute(stmt)
    modifier = result.scalar_one()
    return ModifierResponse.model_validate(modifier)


async def update_option(
    db: AsyncSession,
    modifier_id: uuid.UUID,
    option_id: uuid.UUID,
    data: ModifierOptionUpdate,
) -> ModifierResponse:
    result = await db.execute(
        select(ModifierOption).where(
            ModifierOption.id == option_id,
            ModifierOption.modifier_id == modifier_id,
        )
    )
    option = result.scalar_one_or_none()
    if not option:
        raise NotFoundError(
            f"Option '{option_id}' not found on modifier '{modifier_id}'"
        )

    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(option, key, val)

    await db.flush()
    await _enqueue_products_using(db, modifier_id)

    stmt = (
        select(Modifier)
        .options(*_modifier_load_options())
        .where(Modifier.id == modifier_id)
    )
    result = await db.execute(stmt)
    modifier = result.scalar_one()
    return ModifierResponse.model_validate(modifier)


async def delete_option(
    db: AsyncSession,
    modifier_id: uuid.UUID,
    option_id: uuid.UUID,
) -> ModifierResponse:
    result = await db.execute(
        select(ModifierOption).where(
            ModifierOption.id == option_id,
            ModifierOption.modifier_id == modifier_id,
        )
    )
    option = result.scalar_one_or_none()
    if not option:
        raise NotFoundError(
            f"Option '{option_id}' not found on modifier '{modifier_id}'"
        )

    option.is_active = False
    await db.flush()
    await _enqueue_products_using(db, modifier_id)

    stmt = (
        select(Modifier)
        .options(*_modifier_load_options())
        .where(Modifier.id == modifier_id)
    )
    result = await db.execute(stmt)
    modifier = result.scalar_one()
    return ModifierResponse.model_validate(modifier)
