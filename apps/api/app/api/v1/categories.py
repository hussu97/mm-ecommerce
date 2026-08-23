import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete_pattern, cache_get, cache_set
from app.core.deps import browsing_branch, get_db, get_optional_user
from app.core.exceptions import ForbiddenError
from app.core.permissions import require
from app.models.user import User
from app.schemas.category import CategoryCreate, CategoryResponse, CategoryUpdate
from app.services import audit_service, category_service, indexnow_service

router = APIRouter()

# Keyed by what it holds, not just "active": the list excludes categories with
# nothing to sell on the channel that asked. The channel is part of the key —
# serving the storefront's list to the register is the bug this endpoint had.
_CACHE_KEY = "categories:channel:{channel}"
_CACHE_TTL = 300
#: Every cache key this module writes, for invalidation on a write.
_CACHED_CHANNELS = ("web", "pos")


async def _invalidate() -> None:
    """
    Retire every channel's list. A category edit can change any of them.

    A pattern rather than the exact keys, because the web list is now keyed by
    branch as well: one entry per kitchen plus the estate-wide one. Deleting the
    three names this used to build would leave every branch-scoped copy up for
    its whole TTL, which is a category edit that reaches nobody who has told us
    where they are.
    """
    for channel in _CACHED_CHANNELS:
        await cache_delete_pattern(_CACHE_KEY.format(channel=channel) + "*")
    # Featured products are also category-scoped. Without this, hiding a
    # category leaves its feature cards up until their independent TTL ends.
    await cache_delete_pattern("products:featured:*")


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
    branch: uuid.UUID | None = Depends(browsing_branch),
    db: AsyncSession = Depends(get_db),
    viewer: User | None = Depends(get_optional_user),
):
    """
    List categories with product counts.

    A category is listed for a selling channel only when it has something to
    sell there — so the website never shows an empty "Juices" and, just as
    importantly, the register does show it.
    """
    is_catalogue_staff = bool(viewer and (viewer.is_staff or viewer.is_admin))
    if channel not in (None, "web") and not is_catalogue_staff:
        raise ForbiddenError("Staff access is required for the POS catalogue")
    if not is_catalogue_staff:
        include_inactive = False
        channel = "web"

    resolved = category_service.resolve_channel(channel, include_inactive)
    # Keyed by branch, because two kitchens have two counts and the nav is drawn
    # from this. One entry serving both would put a chip on the nav for a
    # category the shopper's own branch has nothing in.
    cache_key = _CACHE_KEY.format(channel=f"{resolved}:{branch or 'any'}")
    cacheable = not include_inactive and resolved in _CACHED_CHANNELS

    if cacheable:
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached

    result = await category_service.get_all(
        db, include_inactive=include_inactive, channel=resolved, branch_id=branch
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
    viewer: User | None = Depends(get_optional_user),
):
    """Get a single category by slug."""
    if channel != "web" and not (viewer and (viewer.is_staff or viewer.is_admin)):
        raise ForbiddenError("Staff access is required for the POS catalogue")
    return await category_service.get_by_slug(db, slug, channel=channel)


@router.post("", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category(
    request: Request,
    data: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("catalogue.manage")),
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
    admin: User = Depends(require("catalogue.manage")),
):
    """Update a category (admin only)."""
    result = await category_service.update(db, slug, data)
    await _invalidate()
    # A rename moves the listing page and every product URL beneath it. The old
    # addresses are handled by the redirect `record_rename` wrote; this is the
    # other half — telling the engines the new ones exist, rather than waiting
    # for a crawler to follow a 308 to find out.
    if data.slug and data.slug != slug:
        indexnow_service.submit_in_background(
            indexnow_service.category_urls(result.slug)
            + [
                url
                for product_slug in await category_service.product_slugs(
                    db, result.slug
                )
                for url in indexnow_service.product_urls(result.slug, product_slug)
            ]
        )
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
    admin: User = Depends(require("catalogue.manage")),
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
