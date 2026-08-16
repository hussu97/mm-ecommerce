from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.permissions import require
from app.models.user import User
from app.schemas.cms import (
    CmsPageLocaleUpdate,
    CmsPagePublicResponse,
    CmsPageResponse,
    CmsPageUpdate,
)
from app.services import cms_service, image_warm_service

router = APIRouter()


@router.get("/pages", response_model=list[CmsPageResponse])
async def list_pages(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("content.manage")),
):
    return await cms_service.list_pages(db)


@router.get("/pages/{slug}", response_model=CmsPageResponse)
async def get_page(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("content.manage")),
):
    return await cms_service.get_page_admin(db, slug)


@router.put("/pages/{slug}", response_model=CmsPageResponse)
async def update_page(
    slug: str,
    data: CmsPageUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("content.manage")),
):
    page = await cms_service.update_page(db, slug, data.content)
    # A swapped hero or promo photograph is a brand-new image URL that no page
    # view has ever asked the optimiser for. Warm it here rather than leaving the
    # first customer after the change to sit through the encode.
    background_tasks.add_task(
        image_warm_service.warm_quietly,
        image_warm_service.collect_image_urls(data.content),
    )
    return page


@router.put("/pages/{slug}/{locale}", response_model=CmsPageResponse)
async def update_page_locale(
    slug: str,
    locale: str,
    data: CmsPageLocaleUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("content.manage")),
):
    page = await cms_service.update_page_locale(db, slug, locale, data.content)
    background_tasks.add_task(
        image_warm_service.warm_quietly,
        image_warm_service.collect_image_urls(data.content),
    )
    return page


@router.get("/public/{slug}", response_model=CmsPagePublicResponse)
async def get_public_page(
    slug: str,
    locale: str = Query(default="en"),
    db: AsyncSession = Depends(get_db),
):
    return await cms_service.get_page_public(db, slug, locale)
