"""
Idempotent seed script for i18n — Language records + UI translations.
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

# (namespace, key, value)
EN_TRANSLATIONS: list[tuple[str, str, str]] = [
    ("nav", "home", "Home"),
    ("nav", "menu", "Menu"),
    ("nav", "about", "About Us"),
    ("nav", "contact", "Contact Us"),
    ("nav", "faq", "FAQ"),
    ("nav", "privacy", "Privacy Policy"),
    ("nav", "cart", "Cart"),
    ("nav", "sign_in", "Sign In"),
    ("nav", "sign_up", "Create Account"),
    ("nav", "sign_out", "Sign Out"),
    ("nav", "my_account", "My Account"),
    ("nav", "brownies", "Brownies"),
    ("nav", "cookies", "Cookies"),
    ("nav", "cookie_melt", "Cookie Melt"),
    ("nav", "mix_boxes", "Mix Boxes"),
    ("nav", "desserts", "Desserts"),
    ("footer", "tagline", "Made with 100% Love"),
    ("footer", "copyright", "All rights reserved"),
    # common
    ("common", "qty", "Qty"),
    ("common", "previous", "Previous"),
    ("common", "next", "Next"),
    ("common", "page_of", "Page {page} of {pages}"),
    # breadcrumb
    ("breadcrumb", "home", "Home"),
    ("breadcrumb", "cart", "Cart"),
    # product
    ("product", "add_to_cart", "Add to Cart"),
    ("product", "adding", "Adding..."),
    ("product", "select_options", "Select Options"),
    ("product", "select_required_options", "Select required options"),
    ("product", "from", "From"),
    ("product", "added_to_cart", "{name} added to cart"),
    ("product", "failed_to_add", "Failed to add to cart"),
    ("product", "pick_exactly", "Pick {n}"),
    ("product", "pick_range", "Pick {min}–{max}"),
    ("product", "up_to", "Up to {n}"),
    ("product", "add_short", "Add"),
    ("product", "select_short", "Select"),
    # cart
    ("cart", "title", "My Cart"),
    ("cart", "item", "item"),
    ("cart", "items", "items"),
    ("cart", "empty_title", "Your cart is empty"),
    (
        "cart",
        "empty_body",
        "Looks like you haven't added anything yet. Explore our handcrafted treats and find something you'll love.",
    ),
    ("cart", "continue_shopping", "Continue Shopping"),
    ("cart", "order_summary", "Order Summary"),
    ("cart", "subtotal", "Subtotal"),
    ("cart", "discount", "Discount"),
    ("cart", "delivery", "Delivery"),
    ("cart", "calculated_at_checkout", "Calculated at checkout"),
    ("cart", "total", "Total"),
    ("cart", "promo_placeholder", "Promo code"),
    ("cart", "apply", "Apply"),
    ("cart", "proceed_to_checkout", "Proceed to Checkout"),
    ("cart", "continue_shopping_link", "← Continue Shopping"),
    ("cart", "failed_update", "Failed to update quantity"),
    ("cart", "failed_remove", "Failed to remove item"),
    ("cart", "promo_applied", 'Promo code "{code}" applied!'),
    ("cart", "invalid_promo", "Invalid promo code"),
    ("cart", "promo_error", "Failed to validate promo code. Please try again."),
    ("cart", "something_wrong", "Something went wrong. Please try again."),
    # promo_banner
    ("promo_banner", "text", "Free delivery on orders over 200 AED!"),
]

AR_TRANSLATIONS: list[tuple[str, str, str]] = [
    # (namespace, key, value)
    ("nav", "home", "الرئيسية"),
    ("nav", "menu", "القائمة"),
    ("nav", "about", "من نحن"),
    ("nav", "contact", "تواصل معنا"),
    ("nav", "faq", "الأسئلة الشائعة"),
    ("nav", "privacy", "سياسة الخصوصية"),
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
    # common
    ("common", "qty", "الكمية"),
    ("common", "previous", "السابق"),
    ("common", "next", "التالي"),
    ("common", "page_of", "صفحة {page} من {pages}"),
    # breadcrumb
    ("breadcrumb", "home", "الرئيسية"),
    ("breadcrumb", "cart", "سلة التسوق"),
    # product
    ("product", "add_to_cart", "أضف للسلة"),
    ("product", "adding", "جاري الإضافة..."),
    ("product", "select_options", "اختر الخيارات"),
    ("product", "select_required_options", "اختر الخيارات المطلوبة"),
    ("product", "from", "من"),
    ("product", "added_to_cart", "أُضيف {name} إلى السلة"),
    ("product", "failed_to_add", "فشل الإضافة إلى السلة"),
    ("product", "pick_exactly", "اختر {n}"),
    ("product", "pick_range", "اختر {min}–{max}"),
    ("product", "up_to", "حتى {n}"),
    ("product", "add_short", "أضف"),
    ("product", "select_short", "اختر"),
    # cart
    ("cart", "title", "سلة التسوق"),
    ("cart", "item", "منتج"),
    ("cart", "items", "منتجات"),
    ("cart", "empty_title", "سلتك فارغة"),
    ("cart", "empty_body", "لم تضف أي شيء بعد. تصفح منتجاتنا الحرفية وجد شيئاً ستحبه."),
    ("cart", "continue_shopping", "متابعة التسوق"),
    ("cart", "order_summary", "ملخص الطلب"),
    ("cart", "subtotal", "المجموع الفرعي"),
    ("cart", "discount", "الخصم"),
    ("cart", "delivery", "التوصيل"),
    ("cart", "calculated_at_checkout", "يحسب عند الدفع"),
    ("cart", "total", "الإجمالي"),
    ("cart", "promo_placeholder", "رمز الخصم"),
    ("cart", "apply", "تطبيق"),
    ("cart", "proceed_to_checkout", "المتابعة للدفع"),
    ("cart", "continue_shopping_link", "→ متابعة التسوق"),
    ("cart", "failed_update", "فشل تحديث الكمية"),
    ("cart", "failed_remove", "فشل حذف المنتج"),
    ("cart", "promo_applied", 'تم تطبيق رمز الخصم "{code}"!'),
    ("cart", "invalid_promo", "رمز الخصم غير صحيح"),
    ("cart", "promo_error", "فشل التحقق من رمز الخصم. حاول مجدداً."),
    ("cart", "something_wrong", "حدث خطأ. حاول مجدداً."),
    # promo_banner
    ("promo_banner", "text", "توصيل مجاني للطلبات التي تتجاوز 200 درهم!"),
]

ALL_TRANSLATIONS = [("en", *row) for row in EN_TRANSLATIONS] + [
    ("ar", *row) for row in AR_TRANSLATIONS
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

    # Translations
    for locale, namespace, key, value in ALL_TRANSLATIONS:
        result = await session.execute(
            select(UiTranslation).where(
                UiTranslation.locale == locale,
                UiTranslation.namespace == namespace,
                UiTranslation.key == key,
            )
        )
        existing = result.scalar_one_or_none()
        if not existing:
            session.add(
                UiTranslation(locale=locale, namespace=namespace, key=key, value=value)
            )
            print(f"  ✅ {locale}:{namespace}.{key}")
        else:
            print(f"  ⏭  {locale}:{namespace}.{key} already exists")

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
