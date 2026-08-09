from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.services import menu_group_service
from app.models.category import Category
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.product import WEB_CHANNEL, Product, sells_on
from app.schemas.product import (
    ProductCreate,
    ProductModifierLink,
    ProductResponse,
    ProductUpdate,
)
from app.services.storefront_visibility import (
    active_website_category_clause,
    website_product_visibility_clause,
)

__all__ = [
    "create",
    "delete",
    "get_all",
    "get_by_slug",
    "get_by_slug_admin",
    "get_by_sku",
    "get_featured",
    "link_modifier",
    "unlink_modifier",
    "update",
]


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _from_price():
    """
    The number the card actually prints, as something the database can sort on.

    Sorting on `base_price` is sorting on a column that is **zero for half the
    catalogue**. Every brownie and every cookie is priced entirely through its
    size modifier — 9 of 9 and 7 of 7 — so on those pages "Price: low to high"
    ordered nine identical zeroes and rendered them in whatever order the
    planner felt like, under a heading promising otherwise. The storefront had
    always shown a sensible "From 55.00 AED" there, because `computeFromPrice`
    walks the modifiers; the sort never did, and nothing noticed until this
    release put a sort control in front of a customer.

    Mirrors `apps/web/lib/pricing.ts::computeFromPrice`, which is the definition
    of the price being displayed:

      1. `base_price`, plus the cheapest active option of every **required**
         group (`minimum_options > 0`) — those are choices the customer cannot
         decline, so their cost is part of the floor.
      2. If that is still zero, the cheapest non-zero active option on **any**
         group. This is the modifier-priced case, where the options are not a
         surcharge on a price but are the price.

    Two correlated subqueries rather than a join, so the ordering cannot
    multiply the rows it is ordering — a product with three modifier groups must
    not appear three times in a catalogue listing.
    """
    cheapest_active = (
        select(func.min(ModifierOption.price))
        .where(
            ModifierOption.modifier_id == ProductModifier.modifier_id,
            ModifierOption.is_active.is_(True),
        )
        .scalar_subquery()
    )

    required_floor = (
        select(func.coalesce(func.sum(cheapest_active), 0))
        .where(
            ProductModifier.product_id == Product.id,
            ProductModifier.minimum_options > 0,
        )
        .scalar_subquery()
    )

    any_priced_option = (
        select(func.min(ModifierOption.price))
        .select_from(ProductModifier)
        .join(ModifierOption, ModifierOption.modifier_id == ProductModifier.modifier_id)
        .where(
            ProductModifier.product_id == Product.id,
            ModifierOption.is_active.is_(True),
            ModifierOption.price > 0,
        )
        .scalar_subquery()
    )

    floor = func.coalesce(Product.base_price, 0) + func.coalesce(required_floor, 0)
    return case((floor > 0, floor), else_=func.coalesce(any_priced_option, 0))


#: `price_asc`/`price_desc` are built per call because `_from_price()` composes
#: correlated subqueries; the rest are plain columns and can be module-level.
_SORT_MAP = {
    "newest": Product.created_at.desc(),
    "oldest": Product.created_at.asc(),
    "name": Product.name.asc(),
    "category": None,  # handled specially — needs outerjoin
}


def _order_for(sort: str):
    if sort == "price_asc":
        # `name` as the tie-break, so two products at the same price hold a
        # stable order across pages instead of the planner reshuffling them and
        # showing the customer one item twice while hiding another.
        return (_from_price().asc(), Product.name.asc())
    if sort == "price_desc":
        return (_from_price().desc(), Product.name.asc())
    return (_SORT_MAP.get(sort, Product.created_at.desc()),)


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
    category_slugs: list[str] | None = None,
    search: str | None = None,
    featured: bool | None = None,
    sort: str = "newest",
    page: int = 1,
    per_page: int = 20,
    include_inactive: bool = False,
    is_active: bool | None = None,
    channel: str = "web",
) -> tuple[list[ProductResponse], int]:
    """
    List products for one sales channel.

    `channel` defaults to "web" on purpose. The storefront and the terminal
    share this query, and defaulting the other way would mean a forgotten
    parameter puts bottled water on a cake website. Asking for the POS
    catalogue is explicit; leaking to the web is not possible by omission.
    """
    # Built in two halves on purpose. Everything that decides *which* rows match
    # goes on `stmt`; the ordering and the eager loads go on afterwards, and only
    # onto the query that actually returns rows.
    #
    # The count used to be taken off the finished statement, ordering and all.
    # `ORDER BY` inside a subquery is not dropped by the planner, so `price_asc`
    # had Postgres evaluate `_from_price()` — two correlated subqueries over
    # product_modifiers and modifier_options — for every row in the catalogue
    # and then sort them, all to produce a number that cannot depend on order.
    # `joinedload(Product.category)` was adding a LEFT JOIN to it too.
    stmt = select(Product)

    if sort == "category":
        stmt = stmt.outerjoin(Category, Product.category_id == Category.id)

    if is_active is not None:
        stmt = stmt.where(Product.is_active == is_active)  # noqa: E712
    elif not include_inactive:
        stmt = stmt.where(Product.is_active == True)  # noqa: E712

    if channel == "web":
        stmt = stmt.where(sells_on(WEB_CHANNEL), active_website_category_clause())
    elif channel == "pos":
        # The per-product flag and the menu tree together — see
        # menu_group_service.pos_visibility_clause for why membership only
        # gates once a tree actually exists.
        stmt = stmt.where(menu_group_service.pos_visibility_clause())

    if category_slugs:
        if sort != "category":
            stmt = stmt.join(Product.category).where(Category.slug.in_(category_slugs))
        else:
            stmt = stmt.where(Category.slug.in_(category_slugs))

    if search:
        stmt = stmt.where(Product.name.ilike(f"%{_escape_like(search)}%", escape="\\"))

    if featured is not None:
        stmt = stmt.where(Product.is_featured == featured)

    # Count the matching rows — no ordering, no loaders, nothing to sort.
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    # Now the page itself, which is the only query that needs either.
    if sort == "category":
        stmt = stmt.order_by(Category.name.asc(), Product.name.asc())
    else:
        stmt = stmt.order_by(*_order_for(sort))

    offset = (page - 1) * per_page
    stmt = stmt.options(*_product_load_options()).offset(offset).limit(per_page)
    result = await db.execute(stmt)
    products = result.scalars().unique().all()

    return [ProductResponse.model_validate(p) for p in products], total


async def get_by_slug(db: AsyncSession, slug: str) -> ProductResponse:
    stmt = (
        select(Product)
        .options(*_product_load_options())
        .where(
            Product.slug == slug,
            *website_product_visibility_clause(),
        )
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
        .where(
            Product.is_featured == True,  # noqa: E712
            *website_product_visibility_clause(),
        )
        .order_by(Product.display_order, Product.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    products = result.scalars().unique().all()
    return [ProductResponse.model_validate(p) for p in products]


async def get_cart_addons(db: AsyncSession, limit: int = 8) -> list[ProductResponse]:
    """
    The small things the cart offers alongside the basket — a gift note today.

    Sold-out add-ons are left out rather than shown greyed. The tray is a
    suggestion the customer did not ask for, and a suggestion that cannot be
    taken is just a smaller basket page.
    """
    stmt = (
        select(Product)
        .options(*_product_load_options())
        .where(
            Product.is_cart_addon == True,  # noqa: E712
            *website_product_visibility_clause(),
        )
        .order_by(Product.display_order, Product.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    products = result.scalars().unique().all()
    return [
        ProductResponse.model_validate(p)
        for p in products
        if not (p.is_stock_product and p.stock_quantity <= 0)
    ]


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
    product.is_active = False
    await db.flush()


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
