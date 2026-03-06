from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_db
from app.models.user import User
from app.schemas.cms import (
    CmsPageLocaleUpdate,
    CmsPagePublicResponse,
    CmsPageResponse,
    CmsPageUpdate,
)
from app.services import cms_service

router = APIRouter()


@router.get("/pages", response_model=list[CmsPageResponse])
async def list_pages(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    return await cms_service.list_pages(db)


@router.get("/pages/{slug}", response_model=CmsPageResponse)
async def get_page(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    return await cms_service.get_page_admin(db, slug)


@router.put("/pages/{slug}", response_model=CmsPageResponse)
async def update_page(
    slug: str,
    data: CmsPageUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    return await cms_service.update_page(db, slug, data.content)


@router.put("/pages/{slug}/{locale}", response_model=CmsPageResponse)
async def update_page_locale(
    slug: str,
    locale: str,
    data: CmsPageLocaleUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    return await cms_service.update_page_locale(db, slug, locale, data.content)


@router.get("/public/{slug}", response_model=CmsPagePublicResponse)
async def get_public_page(
    slug: str,
    locale: str = Query(default="en"),
    db: AsyncSession = Depends(get_db),
):
    return await cms_service.get_page_public(db, slug, locale)
