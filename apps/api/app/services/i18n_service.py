from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.language import Language, UiTranslation
from app.schemas.i18n import (
    LanguageCreate,
    LanguageResponse,
    LanguageUpdate,
    TranslationBulkUpsert,
)

__all__ = [
    "bulk_upsert_translations",
    "create_language",
    "delete_language",
    "get_active_languages",
    "get_all_languages",
    "get_translations",
    "update_language",
]


async def get_active_languages(db: AsyncSession) -> list[LanguageResponse]:
    result = await db.execute(
        select(Language)
        .where(Language.is_active.is_(True))
        .order_by(Language.display_order)
    )
    return [LanguageResponse.model_validate(lang) for lang in result.scalars().all()]


async def get_all_languages(db: AsyncSession) -> list[LanguageResponse]:
    result = await db.execute(select(Language).order_by(Language.display_order))
    return [LanguageResponse.model_validate(lang) for lang in result.scalars().all()]


async def get_translations(db: AsyncSession, locale: str) -> dict[str, str]:
    result = await db.execute(
        select(UiTranslation).where(UiTranslation.locale == locale)
    )
    translations: dict[str, str] = {}
    for t in result.scalars().all():
        translations[f"{t.namespace}.{t.key}"] = t.value
    return translations


async def create_language(db: AsyncSession, data: LanguageCreate) -> LanguageResponse:
    existing = await db.get(Language, data.code)
    if existing:
        raise BadRequestError(f"Language '{data.code}' already exists")

    lang = Language(**data.model_dump())
    db.add(lang)
    await db.flush()
    return LanguageResponse.model_validate(lang)


async def update_language(
    db: AsyncSession, code: str, data: LanguageUpdate
) -> LanguageResponse:
    lang = await db.get(Language, code)
    if not lang:
        raise NotFoundError(f"Language '{code}' not found")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(lang, field, value)

    await db.flush()
    return LanguageResponse.model_validate(lang)


async def delete_language(db: AsyncSession, code: str) -> None:
    lang = await db.get(Language, code)
    if not lang:
        raise NotFoundError(f"Language '{code}' not found")
    if lang.is_default:
        raise BadRequestError("Cannot delete the default language")

    await db.execute(delete(UiTranslation).where(UiTranslation.locale == code))
    await db.delete(lang)
    await db.flush()


async def bulk_upsert_translations(
    db: AsyncSession, locale: str, data: TranslationBulkUpsert
) -> int:
    lang = await db.get(Language, locale)
    if not lang:
        raise NotFoundError(f"Language '{locale}' not found")

    count = 0
    for entry in data.translations:
        result = await db.execute(
            select(UiTranslation).where(
                UiTranslation.locale == locale,
                UiTranslation.namespace == data.namespace,
                UiTranslation.key == entry.key,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.value = entry.value
        else:
            t = UiTranslation(
                locale=locale,
                namespace=data.namespace,
                key=entry.key,
                value=entry.value,
            )
            db.add(t)
        count += 1

    await db.flush()
    return count
