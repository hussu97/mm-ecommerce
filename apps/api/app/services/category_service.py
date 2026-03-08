from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.category import Category
from app.models.product import Product
from app.schemas.category import CategoryCreate, CategoryResponse, CategoryUpdate


async def _with_product_count(db: AsyncSession, cat: Category) -> CategoryResponse:
    count_result = await db.execute(
        select(func.count(Product.id)).where(
            Product.category_id == cat.id,
            Product.is_active == True,  # noqa: E712
        )
    )
    count = count_result.scalar() or 0
    response = CategoryResponse.model_validate(cat)
    response.product_count = count
    return response


async def get_all(
    db: AsyncSession, include_inactive: bool = False
) -> list[CategoryResponse]:
    stmt = (
        select(Category, func.count(Product.id).label("product_count"))
        .outerjoin(
            Product,
            (Product.category_id == Category.id) & (Product.is_active == True),  # noqa: E712
        )
        .group_by(Category.id)
        .order_by(Category.display_order, Category.name)
    )
    if not include_inactive:
        stmt = stmt.where(Category.is_active == True)  # noqa: E712

    result = await db.execute(stmt)
    rows = result.all()

    categories = []
    for cat, count in rows:
        response = CategoryResponse.model_validate(cat)
        response.product_count = count or 0
        categories.append(response)
    return categories


async def get_by_slug(
    db: AsyncSession, slug: str, include_inactive: bool = False
) -> CategoryResponse:
    stmt = (
        select(Category, func.count(Product.id).label("product_count"))
        .outerjoin(
            Product,
            (Product.category_id == Category.id) & (Product.is_active == True),  # noqa: E712
        )
        .where(Category.slug == slug)
        .group_by(Category.id)
    )
    result = await db.execute(stmt)
    row = result.first()
    if not row:
        raise NotFoundError(f"Category '{slug}' not found")
    cat, count = row
    if not include_inactive and not cat.is_active:
        raise NotFoundError(f"Category '{slug}' not found")
    response = CategoryResponse.model_validate(cat)
    response.product_count = count or 0
    return response


async def create(db: AsyncSession, data: CategoryCreate) -> CategoryResponse:
    existing = await db.execute(select(Category).where(Category.slug == data.slug))
    if existing.scalar_one_or_none():
        raise ConflictError(f"Category with slug '{data.slug}' already exists")

    cat = Category(**data.model_dump())
    db.add(cat)
    await db.flush()
    await db.refresh(cat)

    response = CategoryResponse.model_validate(cat)
    response.product_count = 0
    return response


async def update(db: AsyncSession, slug: str, data: CategoryUpdate) -> CategoryResponse:
    result = await db.execute(select(Category).where(Category.slug == slug))
    cat = result.scalar_one_or_none()
    if not cat:
        raise NotFoundError(f"Category '{slug}' not found")

    updates = data.model_dump(exclude_unset=True)
    new_slug = updates.get("slug")
    if new_slug and new_slug != slug:
        existing = await db.execute(select(Category).where(Category.slug == new_slug))
        if existing.scalar_one_or_none():
            raise ConflictError(f"Category with slug '{new_slug}' already exists")

    for key, val in updates.items():
        setattr(cat, key, val)

    await db.flush()

    stmt = (
        select(Category, func.count(Product.id).label("product_count"))
        .outerjoin(
            Product,
            (Product.category_id == Category.id) & (Product.is_active == True),  # noqa: E712
        )
        .where(Category.id == cat.id)
        .group_by(Category.id)
    )
    row = (await db.execute(stmt)).first()
    updated_cat, count = row
    response = CategoryResponse.model_validate(updated_cat)
    response.product_count = count or 0
    return response


async def delete(db: AsyncSession, slug: str) -> None:
    result = await db.execute(select(Category).where(Category.slug == slug))
    cat = result.scalar_one_or_none()
    if not cat:
        raise NotFoundError(f"Category '{slug}' not found")
    cat.is_active = False
    await db.flush()
