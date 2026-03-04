from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.product import Product, ProductVariant
from app.schemas.product import (
    ProductCreate,
    ProductResponse,
    ProductUpdate,
    ProductVariantCreate,
    ProductVariantResponse,
    ProductVariantUpdate,
)


_SORT_MAP = {
    "newest": Product.created_at.desc(),
    "oldest": Product.created_at.asc(),
    "price_asc": Product.base_price.asc(),
    "price_desc": Product.base_price.desc(),
    "name": Product.name.asc(),
}


def _product_load_options():
    return [
        selectinload(Product.variants),
        joinedload(Product.category),
    ]


async def get_all(
    db: AsyncSession,
    *,
    category_slug: str | None = None,
    search: str | None = None,
    featured: bool | None = None,
    sort: str = "newest",
    page: int = 1,
    per_page: int = 20,
    include_inactive: bool = False,
) -> tuple[list[ProductResponse], int]:
    from app.models.category import Category  # avoid circular at module level

    stmt = (
        select(Product)
        .options(*_product_load_options())
        .order_by(_SORT_MAP.get(sort, Product.created_at.desc()))
    )

    if not include_inactive:
        stmt = stmt.where(Product.is_active == True)  # noqa: E712

    if category_slug:
        stmt = stmt.join(Product.category).where(Category.slug == category_slug)

    if search:
        stmt = stmt.where(Product.name.ilike(f"%{search}%"))

    if featured is not None:
        stmt = stmt.where(Product.is_featured == featured)

    # Total count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    # Paginated results
    offset = (page - 1) * per_page
    stmt = stmt.offset(offset).limit(per_page)
    result = await db.execute(stmt)
    products = result.scalars().unique().all()

    return [ProductResponse.model_validate(p) for p in products], total


async def get_by_slug(db: AsyncSession, slug: str) -> ProductResponse:
    stmt = (
        select(Product)
        .options(*_product_load_options())
        .where(Product.slug == slug, Product.is_active == True)  # noqa: E712
    )
    result = await db.execute(stmt)
    product = result.scalar_one_or_none()
    if not product:
        raise NotFoundError(f"Product '{slug}' not found")
    return ProductResponse.model_validate(product)


async def get_featured(db: AsyncSession, limit: int = 8) -> list[ProductResponse]:
    stmt = (
        select(Product)
        .options(*_product_load_options())
        .where(Product.is_active == True, Product.is_featured == True)  # noqa: E712
        .order_by(Product.display_order, Product.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    products = result.scalars().unique().all()
    return [ProductResponse.model_validate(p) for p in products]


async def create(db: AsyncSession, data: ProductCreate) -> ProductResponse:
    existing = await db.execute(select(Product).where(Product.slug == data.slug))
    if existing.scalar_one_or_none():
        raise ConflictError(f"Product with slug '{data.slug}' already exists")

    variants_data = data.variants
    product_data = data.model_dump(exclude={"variants"})
    product = Product(**product_data)
    db.add(product)
    await db.flush()

    for v in variants_data:
        # Check SKU uniqueness
        sku_existing = await db.execute(select(ProductVariant).where(ProductVariant.sku == v.sku))
        if sku_existing.scalar_one_or_none():
            raise ConflictError(f"Variant SKU '{v.sku}' already exists")
        variant = ProductVariant(product_id=product.id, **v.model_dump())
        db.add(variant)

    await db.flush()

    # Reload with relationships
    stmt = select(Product).options(*_product_load_options()).where(Product.id == product.id)
    result = await db.execute(stmt)
    product = result.scalar_one()
    return ProductResponse.model_validate(product)


async def update(db: AsyncSession, slug: str, data: ProductUpdate) -> ProductResponse:
    stmt = select(Product).options(*_product_load_options()).where(Product.slug == slug)
    result = await db.execute(stmt)
    product = result.scalar_one_or_none()
    if not product:
        raise NotFoundError(f"Product '{slug}' not found")

    updates = data.model_dump(exclude_unset=True)
    new_slug = updates.get("slug")
    if new_slug and new_slug != slug:
        existing = await db.execute(select(Product).where(Product.slug == new_slug))
        if existing.scalar_one_or_none():
            raise ConflictError(f"Product with slug '{new_slug}' already exists")

    for key, val in updates.items():
        setattr(product, key, val)

    await db.flush()

    stmt = select(Product).options(*_product_load_options()).where(Product.id == product.id)
    result = await db.execute(stmt)
    product = result.scalar_one()
    return ProductResponse.model_validate(product)


async def delete(db: AsyncSession, slug: str) -> None:
    result = await db.execute(select(Product).where(Product.slug == slug))
    product = result.scalar_one_or_none()
    if not product:
        raise NotFoundError(f"Product '{slug}' not found")
    await db.delete(product)


# ─── Variant CRUD ────────────────────────────────────────────────────────────

async def get_variant(db: AsyncSession, variant_id: uuid.UUID) -> ProductVariantResponse:
    result = await db.execute(select(ProductVariant).where(ProductVariant.id == variant_id))
    variant = result.scalar_one_or_none()
    if not variant:
        raise NotFoundError(f"Variant '{variant_id}' not found")
    return ProductVariantResponse.model_validate(variant)


async def create_variant(
    db: AsyncSession, product_slug: str, data: ProductVariantCreate
) -> ProductVariantResponse:
    result = await db.execute(select(Product).where(Product.slug == product_slug))
    product = result.scalar_one_or_none()
    if not product:
        raise NotFoundError(f"Product '{product_slug}' not found")

    sku_existing = await db.execute(select(ProductVariant).where(ProductVariant.sku == data.sku))
    if sku_existing.scalar_one_or_none():
        raise ConflictError(f"Variant SKU '{data.sku}' already exists")

    variant = ProductVariant(product_id=product.id, **data.model_dump())
    db.add(variant)
    await db.flush()
    await db.refresh(variant)
    return ProductVariantResponse.model_validate(variant)


async def update_variant(
    db: AsyncSession, variant_id: uuid.UUID, data: ProductVariantUpdate
) -> ProductVariantResponse:
    result = await db.execute(select(ProductVariant).where(ProductVariant.id == variant_id))
    variant = result.scalar_one_or_none()
    if not variant:
        raise NotFoundError(f"Variant '{variant_id}' not found")

    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(variant, key, val)

    await db.flush()
    await db.refresh(variant)
    return ProductVariantResponse.model_validate(variant)


async def delete_variant(db: AsyncSession, variant_id: uuid.UUID) -> None:
    result = await db.execute(select(ProductVariant).where(ProductVariant.id == variant_id))
    variant = result.scalar_one_or_none()
    if not variant:
        raise NotFoundError(f"Variant '{variant_id}' not found")
    await db.delete(variant)
