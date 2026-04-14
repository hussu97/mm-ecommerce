from __future__ import annotations

import math

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, cache_get, cache_set
from app.core.exceptions import NotFoundError
from app.models.blog import BlogPost
from app.schemas.blog import (
    BlogPostCreate,
    BlogPostListPublicResponse,
    BlogPostPublicResponse,
    BlogPostResponse,
    BlogPostUpdate,
)

__all__ = [
    "create_post",
    "get_post_admin",
    "get_post_public",
    "list_posts_admin",
    "list_posts_public",
    "update_post",
    "update_post_locale",
]

_BLOG_TTL = 300  # 5 minutes


def _cache_key(slug: str, locale: str) -> str:
    return f"blog:{slug}:{locale}"


def _list_cache_key(locale: str, page: int, per_page: int) -> str:
    return f"blog:list:{locale}:{page}:{per_page}"


async def list_posts_public(
    db: AsyncSession,
    locale: str,
    page: int = 1,
    per_page: int = 10,
) -> BlogPostListPublicResponse:
    key = _list_cache_key(locale, page, per_page)
    cached = await cache_get(key)
    if cached is not None:
        return BlogPostListPublicResponse(**cached)

    offset = (page - 1) * per_page

    total_result = await db.execute(
        select(func.count()).select_from(BlogPost).where(BlogPost.is_active.is_(True))
    )
    total = total_result.scalar_one()

    result = await db.execute(
        select(BlogPost)
        .where(BlogPost.is_active.is_(True))
        .order_by(BlogPost.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    posts = result.scalars().all()

    items = []
    for post in posts:
        locale_content: dict = post.content.get(locale) or post.content.get("en") or {}
        items.append(
            BlogPostPublicResponse(
                slug=post.slug,
                content=locale_content,
                created_at=post.created_at,
                updated_at=post.updated_at,
            )
        )

    pages = max(1, math.ceil(total / per_page))
    response = BlogPostListPublicResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )
    await cache_set(key, response.model_dump(mode="json"), ttl=_BLOG_TTL)
    return response


async def get_post_public(
    db: AsyncSession, slug: str, locale: str
) -> BlogPostPublicResponse:
    key = _cache_key(slug, locale)
    cached = await cache_get(key)
    if cached is not None:
        return BlogPostPublicResponse(**cached)

    result = await db.execute(
        select(BlogPost).where(BlogPost.slug == slug, BlogPost.is_active.is_(True))
    )
    post = result.scalar_one_or_none()
    if not post:
        raise NotFoundError(f"Blog post '{slug}' not found")

    locale_content: dict = post.content.get(locale) or post.content.get("en") or {}
    response = BlogPostPublicResponse(
        slug=post.slug,
        content=locale_content,
        created_at=post.created_at,
        updated_at=post.updated_at,
    )
    await cache_set(key, response.model_dump(mode="json"), ttl=_BLOG_TTL)
    return response


async def list_posts_admin(db: AsyncSession) -> list[BlogPostResponse]:
    result = await db.execute(select(BlogPost).order_by(BlogPost.created_at.desc()))
    return [BlogPostResponse.model_validate(p) for p in result.scalars().all()]


async def get_post_admin(db: AsyncSession, slug: str) -> BlogPostResponse:
    result = await db.execute(select(BlogPost).where(BlogPost.slug == slug))
    post = result.scalar_one_or_none()
    if not post:
        raise NotFoundError(f"Blog post '{slug}' not found")
    return BlogPostResponse.model_validate(post)


async def create_post(db: AsyncSession, data: BlogPostCreate) -> BlogPostResponse:
    post = BlogPost(slug=data.slug, is_active=data.is_active, content=data.content)
    db.add(post)
    await db.flush()
    return BlogPostResponse.model_validate(post)


async def update_post(
    db: AsyncSession, slug: str, data: BlogPostUpdate
) -> BlogPostResponse:
    result = await db.execute(select(BlogPost).where(BlogPost.slug == slug))
    post = result.scalar_one_or_none()
    if not post:
        raise NotFoundError(f"Blog post '{slug}' not found")

    if data.is_active is not None:
        post.is_active = data.is_active
    if data.content is not None:
        post.content = data.content
        # Invalidate per-locale caches
        for locale in data.content.keys():
            await cache_delete(_cache_key(slug, locale))

    await db.flush()
    return BlogPostResponse.model_validate(post)


async def update_post_locale(
    db: AsyncSession, slug: str, locale: str, locale_content: dict
) -> BlogPostResponse:
    result = await db.execute(select(BlogPost).where(BlogPost.slug == slug))
    post = result.scalar_one_or_none()
    if not post:
        raise NotFoundError(f"Blog post '{slug}' not found")

    updated = dict(post.content)
    updated[locale] = locale_content
    post.content = updated
    await db.flush()

    await cache_delete(_cache_key(slug, locale))
    return BlogPostResponse.model_validate(post)
