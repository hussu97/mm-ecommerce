from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.category import Category
from app.models.product import POS_CHANNEL, WEB_CHANNEL, Product, sells_on
from app.schemas.category import CategoryCreate, CategoryResponse, CategoryUpdate
from app.services import menu_group_service

#: Not a selling channel: the console's view of the whole catalogue.
ALL_CHANNELS = "all"

__all__ = [
    "create",
    "delete",
    "get_all",
    "get_by_slug",
    "update",
]


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


def _countable_products(channel: str):
    """
    The join condition that decides which products a category is counted for.

    The storefront and the register share one product table but not one
    catalogue, so a category has to be counted against the channel doing the
    asking. A category whose every product is POS-only — coffee, bottled
    water — has nothing to sell on the website and must not be listed there.

    The mirror of that was a live bug: the terminal read the same unfiltered
    list, so **Juices** and **Smoothies**, which sell only over the counter,
    had no chip on the register and a cashier could not find their items at
    all. `channel="pos"` counts against exactly what the register can sell,
    menu tree included; `channel="all"` counts every active product, which is
    the catalogue the console exists to manage.
    """
    clause = (Product.category_id == Category.id) & (Product.is_active == True)  # noqa: E712
    if channel == WEB_CHANNEL:
        clause = clause & sells_on(WEB_CHANNEL)
    elif channel == POS_CHANNEL:
        clause = clause & menu_group_service.pos_visibility_clause()
    return clause


def resolve_channel(channel: str | None, include_inactive: bool) -> str:
    """
    Which catalogue the caller means.

    `include_inactive` was the console's way of saying "show me everything"
    before channels were explicit, and both old clients and this module's own
    callers still say only that. It keeps meaning what it meant, so making the
    channel explicit changes nothing for anybody who has not asked for it.
    """
    if channel is not None:
        return channel
    return ALL_CHANNELS if include_inactive else WEB_CHANNEL


def _hides_empty(channel: str) -> bool:
    """
    Whether a category with nothing to sell on this channel is hidden.

    Only for a selling channel. The console lists every category, empty ones
    included — that is where an operator goes to fill them.
    """
    return channel in (WEB_CHANNEL, POS_CHANNEL)


async def get_all(
    db: AsyncSession,
    include_inactive: bool = False,
    channel: str | None = None,
) -> list[CategoryResponse]:
    channel = resolve_channel(channel, include_inactive)
    stmt = (
        select(Category, func.count(Product.id).label("product_count"))
        .outerjoin(Product, _countable_products(channel))
        .group_by(Category.id)
        .order_by(Category.display_order, Category.name)
    )
    if not include_inactive:
        stmt = stmt.where(Category.is_active == True)  # noqa: E712
    if _hides_empty(channel):
        stmt = stmt.having(func.count(Product.id) > 0)

    result = await db.execute(stmt)
    rows = result.all()

    categories = []
    for cat, count in rows:
        response = CategoryResponse.model_validate(cat)
        response.product_count = count or 0
        categories.append(response)
    return categories


async def get_by_slug(
    db: AsyncSession,
    slug: str,
    include_inactive: bool = False,
    channel: str | None = None,
) -> CategoryResponse:
    channel = resolve_channel(channel, include_inactive)
    stmt = (
        select(Category, func.count(Product.id).label("product_count"))
        .outerjoin(Product, _countable_products(channel))
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
    # A category the listing hides must not be reachable by guessing its URL.
    if _hides_empty(channel) and not count:
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
