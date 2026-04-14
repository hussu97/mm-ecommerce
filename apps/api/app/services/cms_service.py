from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, cache_get, cache_set
from app.core.exceptions import NotFoundError
from app.models.cms import CmsPage
from app.schemas.cms import CmsPagePublicResponse, CmsPageResponse

__all__ = [
    "get_page_admin",
    "get_page_public",
    "list_pages",
    "update_page",
    "update_page_locale",
]

_CMS_TTL = 300  # 5 minutes


def _cache_key(slug: str, locale: str) -> str:
    return f"cms:{slug}:{locale}"


async def get_page_public(
    db: AsyncSession, slug: str, locale: str
) -> CmsPagePublicResponse:
    key = _cache_key(slug, locale)
    cached = await cache_get(key)
    if cached is not None:
        return CmsPagePublicResponse(slug=slug, content=cached)

    result = await db.execute(
        select(CmsPage).where(CmsPage.slug == slug, CmsPage.is_active.is_(True))
    )
    page = result.scalar_one_or_none()
    if not page:
        raise NotFoundError(f"CMS page '{slug}' not found")

    locale_content: dict = page.content.get(locale) or page.content.get("en") or {}
    await cache_set(key, locale_content, ttl=_CMS_TTL)
    return CmsPagePublicResponse(slug=slug, content=locale_content)


async def get_page_admin(db: AsyncSession, slug: str) -> CmsPageResponse:
    result = await db.execute(select(CmsPage).where(CmsPage.slug == slug))
    page = result.scalar_one_or_none()
    if not page:
        raise NotFoundError(f"CMS page '{slug}' not found")
    return CmsPageResponse.model_validate(page)


async def list_pages(db: AsyncSession) -> list[CmsPageResponse]:
    result = await db.execute(select(CmsPage).order_by(CmsPage.slug))
    return [CmsPageResponse.model_validate(p) for p in result.scalars().all()]


async def update_page(db: AsyncSession, slug: str, content: dict) -> CmsPageResponse:
    result = await db.execute(select(CmsPage).where(CmsPage.slug == slug))
    page = result.scalar_one_or_none()
    if not page:
        raise NotFoundError(f"CMS page '{slug}' not found")

    page.content = content
    await db.flush()

    # Invalidate all locale caches for this page
    for locale in content.keys():
        await cache_delete(_cache_key(slug, locale))

    return CmsPageResponse.model_validate(page)


async def update_page_locale(
    db: AsyncSession, slug: str, locale: str, locale_content: dict
) -> CmsPageResponse:
    result = await db.execute(select(CmsPage).where(CmsPage.slug == slug))
    page = result.scalar_one_or_none()
    if not page:
        raise NotFoundError(f"CMS page '{slug}' not found")

    # Merge locale into existing content JSONB
    updated = dict(page.content)
    updated[locale] = locale_content
    page.content = updated
    await db.flush()

    await cache_delete(_cache_key(slug, locale))
    return CmsPageResponse.model_validate(page)
