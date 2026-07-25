"""
Generic async CRUD helpers for the POS configuration entities.

The POS domain adds ~20 small admin-managed tables (taxes, payment methods,
charges, reasons, tags, roles, …) that all behave identically: list with an
optional soft-deleted filter, fetch by id, create, update, soft delete, restore.
Hand-writing a service per table would be ~20x the same 60 lines, so the shared
behaviour lives here and only genuinely different logic (device pairing, till
reconciliation, the order engine) gets a bespoke service.
"""

from __future__ import annotations

import uuid
from typing import Any, Sequence, TypeVar

from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.base import Base, utcnow

ModelT = TypeVar("ModelT", bound=Base)


def _supports_soft_delete(model: type[ModelT]) -> bool:
    return hasattr(model, "deleted_at")


def apply_active_filter(
    stmt: Select[Any], model: type[ModelT], include_deleted: bool
) -> Select[Any]:
    if not include_deleted and _supports_soft_delete(model):
        stmt = stmt.where(model.deleted_at.is_(None))  # type: ignore[attr-defined]
    return stmt


def default_order(model: type[ModelT]) -> Sequence[Any]:
    """Prefer an explicit display order, then name, then newest-first."""
    order: list[Any] = []
    if hasattr(model, "display_order"):
        order.append(model.display_order)  # type: ignore[attr-defined]
    if hasattr(model, "name"):
        order.append(model.name)  # type: ignore[attr-defined]
    if not order and hasattr(model, "created_at"):
        order.append(model.created_at.desc())  # type: ignore[attr-defined]
    return order


async def list_all(
    db: AsyncSession,
    model: type[ModelT],
    *,
    include_deleted: bool = False,
    include_inactive: bool = True,
    filters: Sequence[Any] = (),
    options: Sequence[Any] = (),
) -> list[ModelT]:
    stmt = select(model)
    stmt = apply_active_filter(stmt, model, include_deleted)
    if not include_inactive and hasattr(model, "is_active"):
        stmt = stmt.where(model.is_active.is_(True))  # type: ignore[attr-defined]
    for f in filters:
        stmt = stmt.where(f)
    for opt in options:
        stmt = stmt.options(opt)
    order = default_order(model)
    if order:
        stmt = stmt.order_by(*order)
    result = await db.execute(stmt)
    return list(result.scalars().unique().all())


async def paginate(
    db: AsyncSession,
    model: type[ModelT],
    *,
    page: int = 1,
    per_page: int = 50,
    include_deleted: bool = False,
    filters: Sequence[Any] = (),
    options: Sequence[Any] = (),
    order_by: Sequence[Any] = (),
) -> tuple[list[ModelT], int]:
    base = select(model)
    base = apply_active_filter(base, model, include_deleted)
    for f in filters:
        base = base.where(f)

    count_stmt = select(func.count()).select_from(base.subquery())
    total = int((await db.execute(count_stmt)).scalar_one())

    stmt = base
    for opt in options:
        stmt = stmt.options(opt)
    ordering = order_by or default_order(model)
    if ordering:
        stmt = stmt.order_by(*ordering)
    stmt = stmt.offset(max(page - 1, 0) * per_page).limit(per_page)

    rows = list((await db.execute(stmt)).scalars().unique().all())
    return rows, total


async def get_or_404(
    db: AsyncSession,
    model: type[ModelT],
    entity_id: uuid.UUID,
    *,
    options: Sequence[Any] = (),
    include_deleted: bool = False,
) -> ModelT:
    stmt = select(model).where(model.id == entity_id)  # type: ignore[attr-defined]
    stmt = apply_active_filter(stmt, model, include_deleted)
    for opt in options:
        stmt = stmt.options(opt)
    entity = (await db.execute(stmt)).scalars().unique().one_or_none()
    if entity is None:
        raise NotFoundError(f"{model.__name__} not found")
    return entity


async def _flush(db: AsyncSession, label: str) -> None:
    """Turn a unique/FK violation into a 409 instead of a 500."""
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(f"{label} conflicts with an existing record") from exc


async def create(
    db: AsyncSession,
    model: type[ModelT],
    data: BaseModel | dict[str, Any],
    *,
    extra: dict[str, Any] | None = None,
) -> ModelT:
    payload = (
        data.model_dump(exclude_unset=True)
        if isinstance(data, BaseModel)
        else dict(data)
    )
    if extra:
        payload.update(extra)
    entity = model(**payload)  # type: ignore[call-arg]
    db.add(entity)
    await _flush(db, model.__name__)
    await db.refresh(entity)
    return entity


async def update(
    db: AsyncSession,
    entity: ModelT,
    data: BaseModel | dict[str, Any],
    *,
    extra: dict[str, Any] | None = None,
) -> ModelT:
    payload = (
        data.model_dump(exclude_unset=True)
        if isinstance(data, BaseModel)
        else dict(data)
    )
    if extra:
        payload.update(extra)
    for field, value in payload.items():
        setattr(entity, field, value)
    await _flush(db, type(entity).__name__)
    await db.refresh(entity)
    return entity


async def soft_delete(db: AsyncSession, entity: ModelT) -> None:
    """Soft delete where the model supports it, hard delete otherwise."""
    if _supports_soft_delete(type(entity)):
        entity.deleted_at = utcnow()  # type: ignore[attr-defined]
        if hasattr(entity, "is_active"):
            entity.is_active = False  # type: ignore[attr-defined]
        await db.flush()
    else:
        await db.delete(entity)
        await db.flush()


async def restore(db: AsyncSession, entity: ModelT) -> ModelT:
    if _supports_soft_delete(type(entity)):
        entity.deleted_at = None  # type: ignore[attr-defined]
        if hasattr(entity, "is_active"):
            entity.is_active = True  # type: ignore[attr-defined]
        await _flush(db, type(entity).__name__)
        await db.refresh(entity)
    return entity


async def reference_taken(
    db: AsyncSession,
    model: type[ModelT],
    field: str,
    value: str,
    *,
    exclude_id: uuid.UUID | None = None,
) -> bool:
    column = getattr(model, field)
    stmt = select(func.count()).select_from(model).where(column == value)
    if exclude_id is not None:
        stmt = stmt.where(model.id != exclude_id)  # type: ignore[attr-defined]
    if _supports_soft_delete(model):
        stmt = stmt.where(model.deleted_at.is_(None))  # type: ignore[attr-defined]
    return int((await db.execute(stmt)).scalar_one()) > 0
