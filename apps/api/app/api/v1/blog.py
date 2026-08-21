from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.permissions import require
from app.models.user import User
from app.schemas.blog import (
    BlogPostCreate,
    BlogPostListPublicResponse,
    BlogPostLocaleUpdate,
    BlogPostPublicResponse,
    BlogPostResponse,
    BlogPostUpdate,
)
from app.services import blog_service, indexnow_service

router = APIRouter()


# ─── Public endpoints ──────────────────────────────────────────────────────────


@router.get("/public", response_model=BlogPostListPublicResponse)
async def list_posts_public(
    locale: str = Query(default="en"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    return await blog_service.list_posts_public(db, locale, page, per_page)


@router.get("/public/{slug}", response_model=BlogPostPublicResponse)
async def get_post_public(
    slug: str,
    locale: str = Query(default="en"),
    db: AsyncSession = Depends(get_db),
):
    return await blog_service.get_post_public(db, slug, locale)


# ─── Admin endpoints ───────────────────────────────────────────────────────────


@router.get("/posts", response_model=list[BlogPostResponse])
async def list_posts_admin(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("content.manage")),
):
    return await blog_service.list_posts_admin(db)


@router.get("/posts/{slug}", response_model=BlogPostResponse)
async def get_post_admin(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("content.manage")),
):
    return await blog_service.get_post_admin(db, slug)


def _announce(slug: str) -> None:
    """
    Tell the search engines a post changed.

    A blog post is the one thing on this site written specifically to be found
    and quoted, so it is the page where waiting for a crawler costs the most.
    Fire-and-forget: nothing about publishing should wait on, or fail because
    of, a search engine.
    """
    indexnow_service.submit_in_background(indexnow_service.blog_urls(slug))


@router.post("/posts", response_model=BlogPostResponse, status_code=201)
async def create_post(
    data: BlogPostCreate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("content.manage")),
):
    post = await blog_service.create_post(db, data)
    _announce(post.slug)
    return post


@router.put("/posts/{slug}", response_model=BlogPostResponse)
async def update_post(
    slug: str,
    data: BlogPostUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("content.manage")),
):
    post = await blog_service.update_post(db, slug, data)
    _announce(post.slug)
    return post


@router.put("/posts/{slug}/{locale}", response_model=BlogPostResponse)
async def update_post_locale(
    slug: str,
    locale: str,
    data: BlogPostLocaleUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("content.manage")),
):
    post = await blog_service.update_post_locale(db, slug, locale, data.content)
    _announce(post.slug)
    return post
