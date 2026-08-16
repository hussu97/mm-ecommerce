from __future__ import annotations

import uuid

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import Request

from app.core.cache import cache_delete_pattern, cache_get, cache_set
from app.core.deps import (
    get_current_active_user,
    get_db,
    get_optional_user,
)
from app.core.exceptions import ForbiddenError
from app.core.permissions import require
from app.models.menu import BranchProduct
from app.models.user import User
from app.schemas.product import (
    ProductCreate,
    ProductModifierLink,
    ProductResponse,
    ProductUpdate,
)
from app.services import audit_service, product_service

router = APIRouter()

_FEATURED_TTL = 300


async def _invalidate_catalogue_caches() -> None:
    """
    Retire what a product edit can have changed.

    Not only the featured list: whether a category is listed at all depends on
    whether it still has something to sell on that channel, so flipping the
    last item in "Juices" off the register has to retire the register's
    category list too. Otherwise the chip survives its own contents for the
    rest of the TTL and the cashier taps into an empty grid.
    """
    await cache_delete_pattern("products:featured:*")
    await cache_delete_pattern("products:cart_addons:*")
    await cache_delete_pattern("categories:channel:*")


class ProductListResponse(BaseModel):
    items: list[ProductResponse]
    total: int
    page: int
    per_page: int
    pages: int


@router.get("", response_model=ProductListResponse)
async def list_products(
    category: list[str] | None = Query(None, description="Filter by category slug"),
    search: str | None = Query(None, description="Search by product name"),
    featured: bool | None = Query(None, description="Filter featured products"),
    sort: str = Query(
        "newest",
        description="Sort order: newest|oldest|price_asc|price_desc|name|category",
    ),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=2000),
    include_inactive: bool = Query(False),
    is_active: bool | None = Query(None),
    channel: Literal["web", "pos", "all"] = Query(
        "web",
        description=(
            "Which catalogue to list. 'web' is the storefront and is the "
            "default so POS-only items can never leak onto the website by a "
            "forgotten parameter; the terminal asks for 'pos'."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    viewer: User | None = Depends(get_optional_user),
):
    """List products with filtering, search, and pagination."""
    # The counter and inactive catalogue are staff-facing. A customer must not
    # be able to turn a UI filter into a visibility bypass by changing query
    # parameters in their browser.
    is_catalogue_staff = bool(viewer and (viewer.is_staff or viewer.is_admin))
    if channel != "web" and not is_catalogue_staff:
        raise ForbiddenError("Staff access is required for the POS catalogue")
    if not is_catalogue_staff:
        include_inactive = False
        is_active = True

    items, total = await product_service.get_all(
        db,
        category_slugs=category,
        search=search,
        featured=featured,
        sort=sort,
        page=page,
        per_page=per_page,
        include_inactive=include_inactive,
        is_active=is_active,
        channel=channel,
    )
    pages = max(1, (total + per_page - 1) // per_page)
    return ProductListResponse(
        items=items, total=total, page=page, per_page=per_page, pages=pages
    )


@router.get("/featured", response_model=list[ProductResponse])
async def list_featured(
    limit: int = Query(8, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Get featured products."""
    cache_key = f"products:featured:{limit}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    result = await product_service.get_featured(db, limit=limit)
    await cache_set(
        cache_key,
        [r.model_dump(mode="json") for r in result],
        ttl=_FEATURED_TTL,
    )
    return result


@router.get("/cart-addons", response_model=list[ProductResponse])
async def list_cart_addons(
    limit: int = Query(8, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """
    Products the cart offers as add-ons.

    Declared above `/{slug}` deliberately: FastAPI matches in order, and below
    it this path would be read as a product whose slug is "cart-addons".
    """
    cache_key = f"products:cart_addons:{limit}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    result = await product_service.get_cart_addons(db, limit=limit)
    await cache_set(
        cache_key,
        [r.model_dump(mode="json") for r in result],
        ttl=_FEATURED_TTL,
    )
    return result


@router.get("/{slug}", response_model=ProductResponse)
async def get_product(
    slug: str,
    db: AsyncSession = Depends(get_db),
    viewer: User | None = Depends(get_optional_user),
):
    """
    Get a product by slug with all modifiers.

    Staff see the product whatever its channel or status; a shopper sees it
    only if it is live on the website. Without the distinction the console
    could list a counter item and then 404 trying to open it — and every
    inactive product was unreachable for editing, which is precisely when
    someone needs to reach it.
    """
    if viewer is not None and (viewer.is_staff or viewer.is_admin):
        return await product_service.get_by_slug_admin(db, slug)
    return await product_service.get_by_slug(db, slug)


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    request: Request,
    data: ProductCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("catalogue.manage")),
):
    """Create a new product (admin only)."""
    result = await product_service.create(db, data)
    await _invalidate_catalogue_caches()
    await audit_service.log_action(
        db,
        action="CREATE",
        entity_type="product",
        entity_id=result.slug,
        entity_label=result.name,
        admin=admin,
        changes={"created": data.model_dump(mode="json")},
        request=request,
    )
    return result


@router.put("/{slug}", response_model=ProductResponse)
async def update_product(
    request: Request,
    slug: str,
    data: ProductUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("catalogue.manage")),
):
    """Update a product (admin only)."""
    result = await product_service.update(db, slug, data)
    await _invalidate_catalogue_caches()
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="product",
        entity_id=slug,
        entity_label=result.name,
        admin=admin,
        changes={"data": data.model_dump(mode="json", exclude_unset=True)},
        request=request,
    )
    return result


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("catalogue.manage")),
):
    """Delete a product (admin only)."""
    await product_service.delete(db, slug)
    await _invalidate_catalogue_caches()
    await audit_service.log_action(
        db,
        action="DELETE",
        entity_type="product",
        entity_id=slug,
        entity_label=slug,
        admin=admin,
        changes={"deleted_slug": slug},
        request=request,
    )


@router.post(
    "/{slug}/modifiers",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
)
async def link_modifier(
    slug: str,
    data: ProductModifierLink,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("catalogue.manage")),
):
    """Link a modifier to a product (admin only)."""
    return await product_service.link_modifier(db, slug, data)


@router.delete("/{slug}/modifiers/{modifier_id}", response_model=ProductResponse)
async def unlink_modifier(
    slug: str,
    modifier_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("catalogue.manage")),
):
    """Unlink a modifier from a product (admin only)."""
    return await product_service.unlink_modifier(db, slug, modifier_id)


# ─── Per-branch availability ──────────────────────────────────────────────────


class BranchProductResponse(BaseModel):
    branch_id: uuid.UUID
    product_id: uuid.UUID
    is_in_stock: bool
    is_active: bool
    price: Decimal | None = None

    model_config = {"from_attributes": True}


class SetAvailabilityRequest(BaseModel):
    branch_id: uuid.UUID
    is_in_stock: bool | None = None
    is_active: bool | None = None
    #: A branch-specific price; null leaves the catalogue price in force.
    price: Decimal | None = Field(None, ge=0)


@router.get("/availability/{branch_id}", response_model=list[BranchProductResponse])
async def list_branch_availability(
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """
    Every product this branch has overridden.

    Absent means "as the catalogue says" — only differences are stored, so a
    branch that sells the standard menu has no rows here at all.
    """
    rows = (
        (
            await db.execute(
                select(BranchProduct).where(BranchProduct.branch_id == branch_id)
            )
        )
        .scalars()
        .all()
    )
    return [BranchProductResponse.model_validate(r) for r in rows]


@router.put("/{product_id}/availability", response_model=BranchProductResponse)
async def set_branch_availability(
    product_id: uuid.UUID,
    data: SetAvailabilityRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """
    Mark a product in or out of stock at one branch, from the terminal.

    This is the "86 it" button: the kitchen runs out of pistachio kunafa and
    the cashier needs it off the menu at this branch within seconds, without
    touching the catalogue every other branch sells from.
    """
    if not user.can("pos.products.availability"):
        raise ForbiddenError("You do not have permission to change availability")

    row = (
        await db.execute(
            select(BranchProduct).where(
                BranchProduct.branch_id == data.branch_id,
                BranchProduct.product_id == product_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = BranchProduct(branch_id=data.branch_id, product_id=product_id)
        db.add(row)

    if data.is_in_stock is not None:
        row.is_in_stock = data.is_in_stock
    if data.is_active is not None:
        row.is_active = data.is_active
    if data.price is not None:
        row.price = data.price

    await db.flush()
    await db.refresh(row)
    return BranchProductResponse.model_validate(row)
