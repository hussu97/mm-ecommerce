from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_db
from app.models.user import User
from app.schemas.i18n import (
    LanguageCreate,
    LanguageResponse,
    LanguageUpdate,
    TranslationBulkUpsert,
)
from app.services import i18n_service

router = APIRouter()


@router.get("/languages", response_model=list[LanguageResponse])
async def list_languages(db: AsyncSession = Depends(get_db)):
    return await i18n_service.get_active_languages(db)


@router.get("/languages/all", response_model=list[LanguageResponse])
async def list_all_languages(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    return await i18n_service.get_all_languages(db)


@router.get("/translations/{locale}", response_model=dict[str, str])
async def get_translations(locale: str, db: AsyncSession = Depends(get_db)):
    return await i18n_service.get_translations(db, locale)


@router.post("/languages", response_model=LanguageResponse, status_code=201)
async def create_language(
    data: LanguageCreate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    return await i18n_service.create_language(db, data)


@router.put("/languages/{code}", response_model=LanguageResponse)
async def update_language(
    code: str,
    data: LanguageUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    return await i18n_service.update_language(db, code, data)


@router.delete("/languages/{code}", status_code=204)
async def delete_language(
    code: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    await i18n_service.delete_language(db, code)


@router.put("/translations/{locale}")
async def bulk_upsert_translations(
    locale: str,
    data: TranslationBulkUpsert,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    count = await i18n_service.bulk_upsert_translations(db, locale, data)
    return {"updated": count}
