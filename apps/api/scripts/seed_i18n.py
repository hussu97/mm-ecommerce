"""
Idempotent seed script for i18n — Language records + Arabic UI translations.
Run: cd apps/api && python -m scripts.seed_i18n
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.language import Language, UiTranslation


LANGUAGES = [
    {
        "code": "en",
        "name": "English",
        "native_name": "English",
        "direction": "ltr",
        "is_default": True,
        "is_active": True,
        "display_order": 0,
    },
    {
        "code": "ar",
        "name": "Arabic",
        "native_name": "العربية",
        "direction": "rtl",
        "is_default": False,
        "is_active": True,
        "display_order": 1,
    },
]

AR_TRANSLATIONS: list[tuple[str, str, str]] = [
    # (namespace, key, value)
    ("nav", "home", "الرئيسية"),
    ("nav", "menu", "القائمة"),
    ("nav", "about", "من نحن"),
    ("nav", "contact", "تواصل معنا"),
    ("nav", "faq", "الأسئلة الشائعة"),
    ("nav", "privacy", "سياسة الخصوصية"),
    ("nav", "terms", "الشروط والأحكام"),
    ("nav", "cart", "سلة التسوق"),
    ("nav", "sign_in", "تسجيل الدخول"),
    ("nav", "sign_up", "إنشاء حساب"),
    ("nav", "sign_out", "تسجيل الخروج"),
    ("nav", "my_account", "حسابي"),
    ("nav", "brownies", "براونيز"),
    ("nav", "cookies", "كوكيز"),
    ("nav", "cookie_melt", "كوكي ميلت"),
    ("nav", "mix_boxes", "صناديق مشكلة"),
    ("nav", "desserts", "حلويات"),
    ("footer", "tagline", "مصنوعة بـ 100% حب"),
    ("footer", "copyright", "جميع الحقوق محفوظة"),
]


async def seed(session: AsyncSession) -> None:
    print("🌱 Seeding i18n data...")

    # Languages
    for lang_data in LANGUAGES:
        result = await session.execute(
            select(Language).where(Language.code == lang_data["code"])
        )
        existing = result.scalar_one_or_none()
        if not existing:
            session.add(Language(**lang_data))
            print(f"  ✅ Language: {lang_data['code']} ({lang_data['name']})")
        else:
            print(f"  ⏭  Language already exists: {lang_data['code']}")

    await session.flush()

    # Arabic translations
    for namespace, key, value in AR_TRANSLATIONS:
        result = await session.execute(
            select(UiTranslation).where(
                UiTranslation.locale == "ar",
                UiTranslation.namespace == namespace,
                UiTranslation.key == key,
            )
        )
        existing = result.scalar_one_or_none()
        if not existing:
            session.add(
                UiTranslation(locale="ar", namespace=namespace, key=key, value=value)
            )
            print(f"  ✅ ar:{namespace}.{key}")
        else:
            print(f"  ⏭  ar:{namespace}.{key} already exists")

    await session.commit()
    print("\n✨ i18n seed complete!")


async def main() -> None:
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://mm_user:mm_password@localhost:5432/mm_ecommerce",
    )
    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        await seed(session)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
