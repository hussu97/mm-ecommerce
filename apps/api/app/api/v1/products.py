from __future__ import annotations


from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_db
from app.models.user import User
from app.schemas.product import (
    ProductCreate,
    ProductResponse,
    ProductUpdate,
)
from app.services import product_service

router = APIRouter()


class ProductListResponse(BaseModel):
    items: list[ProductResponse]
    total: int
    page: int
    per_page: int
    pages: int


@router.get("", response_model=ProductListResponse)
async def list_products(
    category: str | None = Query(None, description="Filter by category slug"),
    search: str | None = Query(None, description="Search by product name"),
    featured: bool | None = Query(None, description="Filter featured products"),
    sort: str = Query(
        "newest", description="Sort order: newest|oldest|price_asc|price_desc|name"
    ),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    include_inactive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """List products with filtering, search, and pagination."""
    items, total = await product_service.get_all(
        db,
        category_slug=category,
        search=search,
        featured=featured,
        sort=sort,
        page=page,
        per_page=per_page,
        include_inactive=include_inactive,
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
    return await product_service.get_featured(db, limit=limit)


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
    return await product_service.create(db, data)


@router.put("/{slug}", response_model=ProductResponse)
async def update_product(
    slug: str,
    data: ProductUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Update a product (admin only)."""
    return await product_service.update(db, slug, data)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Delete a product (admin only)."""
    await product_service.delete(db, slug)
