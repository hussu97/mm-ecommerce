from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, cache_get, cache_set
from app.core.deps import get_admin_user, get_db
from app.models.user import User
from app.schemas.category import CategoryCreate, CategoryResponse, CategoryUpdate
from app.services import audit_service, category_service

router = APIRouter()

# Keyed by what it holds, not just "active": the list excludes categories with
# nothing to sell on the channel that asked. The channel is part of the key —
# serving the storefront's list to the register is the bug this endpoint had.
_CACHE_KEY = "categories:channel:{channel}"
_CACHE_TTL = 300
#: Every cache key this module writes, for invalidation on a write.
_CACHED_CHANNELS = ("web", "pos")


async def _invalidate() -> None:
    """Retire every channel's list. A category edit can change any of them."""
    for channel in _CACHED_CHANNELS:
        await cache_delete(_CACHE_KEY.format(channel=channel))


@router.get("", response_model=list[CategoryResponse])
async def list_categories(
    include_inactive: bool = False,
    channel: Literal["web", "pos", "all"] | None = Query(
        None,
        description=(
            "Which catalogue to count against. 'web' is the storefront and is "
            "the default; the terminal asks for 'pos', which counts only what "
            "the register can actually sell. 'all' is the console's view."
        ),
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    List categories with product counts.

    A category is listed for a selling channel only when it has something to
    sell there — so the website never shows an empty "Juices" and, just as
    importantly, the register does show it.
    """
    resolved = category_service.resolve_channel(channel, include_inactive)
    cache_key = _CACHE_KEY.format(channel=resolved)
    cacheable = not include_inactive and resolved in _CACHED_CHANNELS

    if cacheable:
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached

    result = await category_service.get_all(
        db, include_inactive=include_inactive, channel=resolved
    )

    if cacheable:
        await cache_set(
            cache_key,
            [r.model_dump(mode="json") for r in result],
            ttl=_CACHE_TTL,
        )

    return result


@router.get("/{slug}", response_model=CategoryResponse)
async def get_category(
    slug: str,
    channel: Literal["web", "pos", "all"] = Query("web"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single category by slug."""
    return await category_service.get_by_slug(db, slug, channel=channel)


@router.post("", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category(
    request: Request,
    data: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Create a new category (admin only)."""
    result = await category_service.create(db, data)
    await _invalidate()
    await audit_service.log_action(
        db,
        action="CREATE",
        entity_type="category",
        entity_id=result.slug,
        entity_label=result.name,
        admin=admin,
        changes={"created": data.model_dump(mode="json")},
        request=request,
    )
    return result


@router.put("/{slug}", response_model=CategoryResponse)
async def update_category(
    request: Request,
    slug: str,
    data: CategoryUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Update a category (admin only)."""
    result = await category_service.update(db, slug, data)
    await _invalidate()
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="category",
        entity_id=slug,
        entity_label=result.name,
        admin=admin,
        changes={"data": data.model_dump(mode="json", exclude_unset=True)},
        request=request,
    )
    return result


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Delete a category (admin only). Fails if products are assigned."""
    await category_service.delete(db, slug)
    await _invalidate()
    await audit_service.log_action(
        db,
        action="DELETE",
        entity_type="category",
        entity_id=slug,
        entity_label=slug,
        admin=admin,
        changes={"deleted_slug": slug},
        request=request,
    )
