"""The Arabic search descriptions, filled to the length a search engine keeps.

`297_seo_description_lengths` fixed the English copy and the shortest Arabic
entries. Most of the Arabic CMS and blog descriptions were still 124–149
characters, where search engines keep about 150–160. This rewrites the ten
that were short. Each keeps what it said and adds one true detail: where we
deliver, who it is from, or what the reader gets.

The Arabic homepage keeps its first sentence exactly as it is, because the
storefront puts the live offer straight after it (155 characters with the
current campaign). Only the second sentence grows, and that sentence is what
shows when no campaign is running (138 → 155).

**Guarded, per convention 7.** Each write matches the whole current value,
checked word for word against production while this was written. Once someone
edits the value in the console, the migration matches nothing and does nothing.
The same is true on an older database dump, or if this migration runs twice.

Revision ID: 298_arabic_seo_descriptions
Revises: 297_seo_description_lengths
Create Date: 2026-09-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "298_arabic_seo_descriptions"
down_revision: Union[str, None] = "297_seo_description_lengths"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: (slug, locale, what production serves today, what to write).
CMS: list[tuple[str, str, str, str]] = [
    (
        "about",
        "ar",
        "ملتنج مومنتس مخبز في الشارقة تديره فاطمة عباسي. براوني وكوكيز وحلويات تُخبز عند الطلب وتُوصَّل إلى دبي والشارقة وبقية الإمارات.",
        "ملتنج مومنتس مخبز في الشارقة تديره فاطمة عباسي. براوني وكوكيز وكوكي ملت وحلويات تُخبز طازجة عند الطلب وتُوصَّل إلى دبي والشارقة وعجمان وبقية الإمارات.",
    ),
    (
        "contact",
        "ar",
        "راسل ملتنج مومنتس على واتساب 3052 554 55 971+ لطلب الكيك المخصص وعلب الحلويات وهدايا الشركات. مقرّنا الشارقة، ونوصّل في أنحاء الإمارات.",
        "راسل ملتنج مومنتس على واتساب 3052 554 55 971+ لطلب الكيك المخصص وعلب الحلويات وهدايا الشركات. مقرّنا في الشارقة، ونوصّل إلى دبي وعجمان وأنحاء الإمارات.",
    ),
    (
        "home",
        "ar",
        "براوني طري وكوكيز وكوكي ملت وكيك، تُخبز عند الطلب في مطبخنا بالشارقة وتُوصَّل إلى دبي وعجمان وكل الإمارات. توصيل مجاني داخل مدينة الشارقة.",
        "براوني طري وكوكيز وكوكي ملت وكيك، تُخبز عند الطلب في مطبخنا بالشارقة وتُوصَّل إلى دبي وعجمان وكل الإمارات. توصيل مجاني داخل مدينة الشارقة، والاستلام مجاني.",
    ),
    (
        "faq",
        "ar",
        "كيف يعمل التوصيل في دبي والشارقة وبقية الإمارات، وكم من الوقت نحتاج قبل الطلب، وما طرق الدفع المقبولة، وما المكونات في مطبخنا. ملتنج مومنتس، الشارقة.",
        "كيف يعمل التوصيل في دبي والشارقة وبقية الإمارات، وكم من الوقت نحتاج قبل الطلب، وما طرق الدفع المقبولة، وما المكونات في مطبخنا. ملتنج مومنتس كيكس، الشارقة.",
    ),
]

#: (slug, locale, what production serves today, what to write).
BLOG: list[tuple[str, str, str, str]] = [
    (
        "eggless-baking-what-changes",
        "ar",
        "ماذا يفعل البيض في الخبز، وما الذي يحل محله، وأي الوصفات تتحوّل جيداً — وكيف تطلب براوني وكوكيز خالياً من البيض في الإمارات.",
        "ماذا يفعل البيض في الخبز، وما الذي يحل محله، وأي الوصفات تتحوّل جيداً — وكيف تطلب براوني وكوكيز خالياً من البيض من ملتنج مومنتس في دبي والشارقة وبقية الإمارات.",
    ),
    (
        "why-we-use-premium-ingredients",
        "ar",
        "الزبدة مقابل المارغرين، والخلاصة الطبيعية مقابل الصناعية، والشوكولاتة الحقيقية مقابل المركّبة — ماذا يفعل كل تبديل، ولماذا لا نفعله.",
        "الزبدة مقابل المارغرين، والخلاصة الطبيعية مقابل الصناعية، والشوكولاتة الحقيقية مقابل المركّبة — ماذا يفعل كل تبديل في الخبز، ولماذا لا نفعله في ملتنج مومنتس.",
    ),
    (
        "eid-and-ramadan-dessert-boxes",
        "ar",
        "تخطيط علب حلويات رمضان والعيد في الإمارات — ماذا تطلب للإفطار والإهداء والمجلس، وكم من الوقت تحتاج، وكيف يعمل التوصيل للطلبات الكبيرة.",
        "تخطيط علب حلويات رمضان والعيد في الإمارات — ماذا تطلب للإفطار والإهداء والمجلس، وكم من الوقت تحتاج للحجز مسبقاً، وكيف يعمل التوصيل لطلبات الهدايا الكبيرة.",
    ),
    (
        "birthday-and-corporate-orders",
        "ar",
        "كيف تطلب علب أعياد الميلاد المخصصة وطاولات الحلويات وتوزيعات الأعراس وهدايا الشركات من ملتنج مومنتس — المهل والكميات والتوصيل في الإمارات.",
        "كيف تطلب علب أعياد الميلاد المخصصة وطاولات الحلويات وتوزيعات الأعراس وهدايا الشركات من ملتنج مومنتس — المهل والكميات المطلوبة والتوصيل إلى أنحاء الإمارات.",
    ),
    (
        "dessert-delivery-across-the-uae",
        "ar",
        "كيف يعمل توصيل الحلويات في دبي والشارقة وأبوظبي وبقية الإمارات في حرّ الصيف — التغليف وتنظيم الجولات ورسوم التوصيل وطريقة الحفظ بعد الوصول.",
        "كيف يعمل توصيل الحلويات في دبي والشارقة وأبوظبي وبقية الإمارات في حرّ الصيف — التغليف وتنظيم الجولات ورسوم التوصيل وطريقة حفظ الحلويات بعد وصولها إليك.",
    ),
    (
        "the-art-of-the-perfect-brownie",
        "ar",
        "لماذا يخرج البراوني كيكياً، والأمور الثلاثة التي تصلحه — الدقيق، والشوكولاتة المذابة، وإخراج الصينية مبكراً. من مطبخ ملتنج مومنتس في الشارقة.",
        "لماذا يخرج البراوني كيكياً، والأمور الثلاثة التي تصلحه — الدقيق، والشوكولاتة المذابة، وإخراج الصينية مبكراً. نصائح عملية من مطبخ ملتنج مومنتس في الشارقة.",
    ),
]


def _rewrite(
    table: str, path: str, rows: list[tuple[str, str, str, str]], reverse: bool
) -> None:
    """Swap one JSON leaf, only where it still holds exactly the old value.

    Same statement as `297_seo_description_lengths`, including the path passed
    as a list (asyncpg sends a str bound to a text[] as an array of characters).
    """
    conn = op.get_bind()
    for slug, locale, old, new in rows:
        if reverse:
            old, new = new, old
        conn.execute(
            sa.text(
                f"UPDATE {table} SET content = jsonb_set(content, "
                f"CAST(:path AS text[]), to_jsonb(CAST(:new AS text))), updated_at = now() "
                f"WHERE slug = :slug AND content #>> CAST(:path AS text[]) = :old"
            ),
            {"slug": slug, "path": [locale, *path.split(",")], "old": old, "new": new},
        )


def upgrade() -> None:
    _rewrite("cms_pages", "seo,description", CMS, reverse=False)
    _rewrite("blog_posts", "meta_description", BLOG, reverse=False)


def downgrade() -> None:
    _rewrite("cms_pages", "seo,description", CMS, reverse=True)
    _rewrite("blog_posts", "meta_description", BLOG, reverse=True)
