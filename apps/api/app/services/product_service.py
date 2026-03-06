from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.modifier import Modifier, ProductModifier
from app.models.product import Product
from app.schemas.product import (
    ProductCreate,
    ProductModifierLink,
    ProductResponse,
    ProductUpdate,
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
        selectinload(Product.product_modifiers)
        .joinedload(ProductModifier.modifier)
        .selectinload(Modifier.options),
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


async def get_by_slug_admin(db: AsyncSession, slug: str) -> ProductResponse:
    """Get product by slug without active filter (for admin)."""
    stmt = select(Product).options(*_product_load_options()).where(Product.slug == slug)
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

    if data.sku:
        sku_existing = await db.execute(select(Product).where(Product.sku == data.sku))
        if sku_existing.scalar_one_or_none():
            raise ConflictError(f"Product with SKU '{data.sku}' already exists")

    product = Product(**data.model_dump())
    db.add(product)
    await db.flush()

    # Reload with relationships
    stmt = (
        select(Product)
        .options(*_product_load_options())
        .where(Product.id == product.id)
    )
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

    new_sku = updates.get("sku")
    if new_sku and new_sku != product.sku:
        sku_existing = await db.execute(select(Product).where(Product.sku == new_sku))
        if sku_existing.scalar_one_or_none():
            raise ConflictError(f"Product with SKU '{new_sku}' already exists")

    for key, val in updates.items():
        setattr(product, key, val)

    await db.flush()

    stmt = (
        select(Product)
        .options(*_product_load_options())
        .where(Product.id == product.id)
    )
    result = await db.execute(stmt)
    product = result.scalar_one()
    return ProductResponse.model_validate(product)


async def delete(db: AsyncSession, slug: str) -> None:
    result = await db.execute(select(Product).where(Product.slug == slug))
    product = result.scalar_one_or_none()
    if not product:
        raise NotFoundError(f"Product '{slug}' not found")
    await db.delete(product)


async def link_modifier(
    db: AsyncSession, slug: str, data: ProductModifierLink
) -> ProductResponse:
    stmt = select(Product).options(*_product_load_options()).where(Product.slug == slug)
    result = await db.execute(stmt)
    product = result.scalar_one_or_none()
    if not product:
        raise NotFoundError(f"Product '{slug}' not found")

    # Check modifier exists
    mod_result = await db.execute(
        select(Modifier).where(Modifier.id == data.modifier_id)
    )
    if not mod_result.scalar_one_or_none():
        raise NotFoundError(f"Modifier '{data.modifier_id}' not found")

    # Check no duplicate
    existing = await db.execute(
        select(ProductModifier).where(
            ProductModifier.product_id == product.id,
            ProductModifier.modifier_id == data.modifier_id,
        )
    )
    if existing.scalar_one_or_none():
        raise ConflictError("Modifier already linked to this product")

    pm = ProductModifier(
        product_id=product.id,
        modifier_id=data.modifier_id,
        minimum_options=data.minimum_options,
        maximum_options=data.maximum_options,
        free_options=data.free_options,
        unique_options=data.unique_options,
    )
    db.add(pm)
    await db.flush()

    stmt = (
        select(Product)
        .options(*_product_load_options())
        .where(Product.id == product.id)
    )
    result = await db.execute(stmt)
    product = result.scalar_one()
    return ProductResponse.model_validate(product)


async def unlink_modifier(
    db: AsyncSession, slug: str, modifier_id: str
) -> ProductResponse:
    stmt = select(Product).options(*_product_load_options()).where(Product.slug == slug)
    result = await db.execute(stmt)
    product = result.scalar_one_or_none()
    if not product:
        raise NotFoundError(f"Product '{slug}' not found")

    pm_result = await db.execute(
        select(ProductModifier).where(
            ProductModifier.product_id == product.id,
            ProductModifier.modifier_id == modifier_id,
        )
    )
    pm = pm_result.scalar_one_or_none()
    if not pm:
        raise NotFoundError("Modifier is not linked to this product")

    await db.delete(pm)
    await db.flush()

    stmt = (
        select(Product)
        .options(*_product_load_options())
        .where(Product.id == product.id)
    )
    result = await db.execute(stmt)
    product = result.scalar_one()
    return ProductResponse.model_validate(product)


async def get_by_sku(db: AsyncSession, sku: str) -> ProductResponse | None:
    stmt = select(Product).options(*_product_load_options()).where(Product.sku == sku)
    result = await db.execute(stmt)
    product = result.scalar_one_or_none()
    if not product:
        return None
    return ProductResponse.model_validate(product)
