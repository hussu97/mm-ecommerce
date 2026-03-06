from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, cache_get, cache_set
from app.core.deps import get_admin_user, get_db
from app.models.user import User
from app.schemas.category import CategoryCreate, CategoryResponse, CategoryUpdate
from app.services import category_service

router = APIRouter()

_CACHE_KEY = "categories:active"
_CACHE_TTL = 300


@router.get("", response_model=list[CategoryResponse])
async def list_categories(
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """List all active categories with product counts."""
    if not include_inactive:
        cached = await cache_get(_CACHE_KEY)
        if cached is not None:
            return cached

    result = await category_service.get_all(db, include_inactive=include_inactive)

    if not include_inactive:
        await cache_set(
            _CACHE_KEY,
            [r.model_dump(mode="json") for r in result],
            ttl=_CACHE_TTL,
        )

    return result


@router.get("/{slug}", response_model=CategoryResponse)
async def get_category(slug: str, db: AsyncSession = Depends(get_db)):
    """Get a single category by slug."""
    return await category_service.get_by_slug(db, slug)


@router.post("", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category(
    data: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Create a new category (admin only)."""
    result = await category_service.create(db, data)
    await cache_delete(_CACHE_KEY)
    return result


@router.put("/{slug}", response_model=CategoryResponse)
async def update_category(
    slug: str,
    data: CategoryUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Update a category (admin only)."""
    result = await category_service.update(db, slug, data)
    await cache_delete(_CACHE_KEY)
    return result


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Delete a category (admin only). Fails if products are assigned."""
    await category_service.delete(db, slug)
    await cache_delete(_CACHE_KEY)
