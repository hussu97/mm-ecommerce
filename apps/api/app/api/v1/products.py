from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete_pattern, cache_get, cache_set
from app.core.deps import get_admin_user, get_db
from app.models.user import User
from app.schemas.product import (
    ProductCreate,
    ProductModifierLink,
    ProductResponse,
    ProductUpdate,
)
from app.services import product_service

router = APIRouter()

_FEATURED_TTL = 300


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
    per_page: int = Query(20, ge=1, le=100),
    include_inactive: bool = Query(False),
    is_active: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List products with filtering, search, and pagination."""
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


@router.get("/{slug}", response_model=ProductResponse)
async def get_product(slug: str, db: AsyncSession = Depends(get_db)):
    """Get a product by slug with all modifiers."""
    return await product_service.get_by_slug(db, slug)


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    data: ProductCreate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Create a new product (admin only)."""
    result = await product_service.create(db, data)
    await cache_delete_pattern("products:featured:*")
    return result


@router.put("/{slug}", response_model=ProductResponse)
async def update_product(
    slug: str,
    data: ProductUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Update a product (admin only)."""
    result = await product_service.update(db, slug, data)
    await cache_delete_pattern("products:featured:*")
    return result


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Delete a product (admin only)."""
    await product_service.delete(db, slug)
    await cache_delete_pattern("products:featured:*")


@router.post(
    "/{slug}/modifiers",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
)
async def link_modifier(
    slug: str,
    data: ProductModifierLink,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Link a modifier to a product (admin only)."""
    return await product_service.link_modifier(db, slug, data)


@router.delete("/{slug}/modifiers/{modifier_id}", response_model=ProductResponse)
async def unlink_modifier(
    slug: str,
    modifier_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Unlink a modifier from a product (admin only)."""
    return await product_service.unlink_modifier(db, slug, modifier_id)
