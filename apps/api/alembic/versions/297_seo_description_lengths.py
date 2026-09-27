"""Search descriptions the length a search engine keeps.

Bing Webmaster Tools reported "meta descriptions too short" on 17 pages, and an
audit of every URL in the sitemap found the other half of the problem as well.
Search engines keep roughly 150–160 characters of a description. Below that
the snippet under-sells the page (and Google rewrites it from the body); above
it the end is cut off. On the homepage, the part that got cut off was the
new-customer offer, which is appended last.

The product and category pages are fixed in the storefront
(`apps/web/lib/meta-description.ts`). What is left is CMS copy, which is
content, so it is a migration (convention 7):

* **Too long:** the English homepage (216 characters before the offer, 274
  with it), About (167), FAQ (174), and three blog posts (170, 172, 180). Each
  one is rewritten to fit under 160 and keep what it was saying.
* **Too short:** Privacy (94 / 86), the Arabic FAQ (115), and the Arabic Dubai
  delivery post (116). Each is filled out to about 150 characters.
* **The homepage** now has a short first sentence, because the storefront puts
  the live offer straight after the first sentence and keeps the rest only if
  there is room. The Sharjah free-delivery line moves into its own second
  sentence, so it still shows whenever there is no campaign running.

**Guarded, per convention 7.** Each write matches the whole current value,
checked word for word against production while this was written. Once someone
edits the value in the console, the migration matches nothing and does nothing.
The same is true on an older database dump, or if this migration runs twice.

Revision ID: 297_seo_description_lengths
Revises: 296_drop_custom_order_capacity
Create Date: 2026-09-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "297_seo_description_lengths"
down_revision: Union[str, None] = "296_drop_custom_order_capacity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: (slug, locale, what production serves today, what to write).
CMS: list[tuple[str, str, str, str]] = [
    (
        "home",
        "en",
        "Fudgy brownies, gooey cookies, cookie melts and cakes, baked to order in "
        "our Sharjah kitchen and delivered to Dubai, Sharjah, Ajman and every "
        "emirate. Free delivery in Sharjah city, and from AED 75 in selected areas.",
        "Fudgy brownies, cookies, cookie melts and cakes — baked to order in "
        "Sharjah, delivered across the UAE. Free delivery in Sharjah city.",
    ),
    (
        "home",
        "ar",
        "براوني طري وكوكيز وكوكي ملت وكيك، تُخبز عند الطلب في مطبخنا بالشارقة "
        "وتُوصَّل إلى دبي والشارقة وعجمان وكل الإمارات. توصيل مجاني داخل مدينة "
        "الشارقة، ومن 75 درهماً في مناطق مختارة.",
        "براوني طري وكوكيز وكوكي ملت وكيك، تُخبز عند الطلب في مطبخنا بالشارقة "
        "وتُوصَّل إلى دبي وعجمان وكل الإمارات. توصيل مجاني داخل مدينة الشارقة.",
    ),
    (
        "about",
        "en",
        "Melting Moments is a bakery in Sharjah run by Fatema Abbasi. Brownies, "
        "cookies and desserts baked to order and delivered across Dubai, Sharjah "
        "and the rest of the UAE.",
        "Melting Moments is a Sharjah bakery run by Fatema Abbasi. Brownies, "
        "cookies and desserts baked to order and delivered across Dubai, Sharjah "
        "and the UAE.",
    ),
    (
        "faq",
        "en",
        "How delivery works across Dubai, Sharjah and the rest of the UAE, how "
        "much notice we need, what we take as payment, and what's in the "
        "kitchen. Melting Moments Cakes, Sharjah.",
        "How delivery works across Dubai, Sharjah and the UAE, how much notice we "
        "need, what payment we take and what's in the kitchen. Melting Moments "
        "Cakes, Sharjah.",
    ),
    (
        "faq",
        "ar",
        "كيف يعمل التوصيل في دبي والشارقة وبقية الإمارات، وكم من الوقت نحتاج، "
        "وما طرق الدفع المقبولة. ملتنج مومنتس، الشارقة.",
        "كيف يعمل التوصيل في دبي والشارقة وبقية الإمارات، وكم من الوقت نحتاج قبل "
        "الطلب، وما طرق الدفع المقبولة، وما المكونات في مطبخنا. ملتنج مومنتس، الشارقة.",
    ),
    (
        "privacy",
        "en",
        "Privacy policy for Melting Moments Cakes — how we collect, use and "
        "protect your personal data.",
        "Privacy policy for Melting Moments Cakes — what personal data we collect "
        "when you order, how we use and protect it, who we share it with, and "
        "your rights.",
    ),
    (
        "privacy",
        "ar",
        "سياسة الخصوصية لملتينج مومنتس كيكس — كيف نجمع بياناتك الشخصية ونستخدمها "
        "ونحمي خصوصيتك.",
        "سياسة الخصوصية لملتنج مومنتس كيكس — ما البيانات الشخصية التي نجمعها عند "
        "الطلب، وكيف نستخدمها ونحميها، ومع من نشاركها، وما حقوقك في الوصول إليها "
        "وحذفها.",
    ),
]

#: (slug, locale, what production serves today, what to write).
BLOG: list[tuple[str, str, str, str]] = [
    (
        "birthday-and-corporate-orders",
        "en",
        "How to order custom birthday boxes, dessert tables, wedding favours and "
        "corporate gifting from Melting Moments Cakes — lead times, quantities "
        "and delivery across the UAE.",
        "How to order custom birthday boxes, dessert tables, wedding favours and "
        "corporate gifts from Melting Moments — lead times, quantities and UAE "
        "delivery.",
    ),
    (
        "dessert-delivery-across-the-uae",
        "en",
        "How dessert delivery works across Dubai, Sharjah, Abu Dhabi and the rest "
        "of the UAE in summer heat — packing, run planning, delivery fees and how "
        "to store it when it lands.",
        "How dessert delivery works across Dubai, Sharjah, Abu Dhabi and the UAE "
        "in summer heat — packing, run planning, delivery fees and storing it on "
        "arrival.",
    ),
    (
        "dessert-delivery-dubai-what-travels-well",
        "en",
        "A guide to dessert delivery in Dubai — which brownies, cookies and "
        "desserts travel well, how much notice to give, and how delivery is "
        "priced. From Melting Moments Cakes in Sharjah.",
        "A guide to dessert delivery in Dubai — which brownies, cookies and "
        "desserts travel well, how much notice to give, and how delivery is "
        "priced by Melting Moments.",
    ),
    (
        "dessert-delivery-dubai-what-travels-well",
        "ar",
        "دليل توصيل الحلويات في دبي — أي براوني وكوكيز وحلويات تتحمّل الطريق، "
        "وكم من الإشعار تحتاج، وكيف تُحتسب رسوم التوصيل.",
        "دليل توصيل الحلويات في دبي — أي براوني وكوكيز وحلويات تتحمّل الطريق، "
        "وكم من الإشعار تحتاج قبل الطلب، وكيف تُحتسب رسوم التوصيل. من ملتنج "
        "مومنتس في الشارقة.",
    ),
]


def _rewrite(
    table: str, path: str, rows: list[tuple[str, str, str, str]], reverse: bool
) -> None:
    """Swap one JSON leaf, only where it still holds exactly the old value."""
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
            # A list, not a "{en,seo,description}" literal: under asyncpg the
            # CAST makes this parameter a text[], and a str is iterable, so it
            # would be sent as an array of single characters.
            {"slug": slug, "path": [locale, *path.split(",")], "old": old, "new": new},
        )


def upgrade() -> None:
    _rewrite("cms_pages", "seo,description", CMS, reverse=False)
    _rewrite("blog_posts", "meta_description", BLOG, reverse=False)


def downgrade() -> None:
    _rewrite("cms_pages", "seo,description", CMS, reverse=True)
    _rewrite("blog_posts", "meta_description", BLOG, reverse=True)
