"""Give the home page a hero carousel, a USP strip, category tiles and promo bands.

The old home row held one flat hero (tagline / headline / body / two buttons)
and three static sections. The redesigned page is built from CMS-owned blocks:
a multi-slide hero, a scrolling USP marquee, category tiles, repeatable promo
bands, and a `layout` key that decides the order.

Existing copy is *not* thrown away. The new defaults are merged underneath what
is already in the row, so anything the shop has edited since 011 wins and only
the genuinely new keys are added. The one exception is the flat hero: it has no
place in the new shape, so its keys are dropped after the merge — the carousel
carries the same words on its first slide.

Revision ID: 049_home_page_sections
Revises: 048_delivery_polygons
Create Date: 2026-08-02 00:00:00.000000

"""

from __future__ import annotations

import json
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "049_home_page_sections"
down_revision: Union[str, None] = "048_delivery_polygons"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BANNERS = "/images/banners"
TILES = "/images/tiles"
PHOTOS = "/images/photos"

LEGACY_HERO_KEYS = (
    "tagline",
    "headline",
    "body",
    "shop_button_text",
    "shop_button_href",
    "story_button_text",
    "story_button_href",
)

SECTION_ORDER = ["hero", "usps", "featured", "categories", "promos", "baker", "cater"]

CATEGORY_TILES = [
    ("cat-cookiemelt", "cookiemelt"),
    ("cat-brownies", "brownies"),
    ("cat-cookies", "cookies"),
    ("cat-mixboxes", "mixboxes"),
    ("cat-cakes", "cakes"),
    ("cat-desserts", "desserts"),
    ("cat-eggless", "eggless"),
    ("cat-extras", "extras"),
]

KITCHEN_GALLERY = [
    {"image": f"{PHOTOS}/person_shot_2.png", "alt": "Freshly baked brownies"},
    {"image": f"{PHOTOS}/person_shot_3.png", "alt": "Handcrafted cookies"},
    {"image": f"{PHOTOS}/person_shot_4.jpg", "alt": "Artisanal desserts"},
]


def _tiles() -> list[dict[str, str]]:
    return [
        {"slug": slug, "image": f"{TILES}/tile-{art}.jpg"}
        for slug, art in CATEGORY_TILES
    ]


HOME_EN: dict[str, Any] = {
    "layout": {"order": SECTION_ORDER, "hidden": []},
    "hero": {
        "autoplay_ms": 6000,
        "slides": [
            {
                "image": f"{BANNERS}/hero-cookie-melt.jpg",
                "image_mobile": f"{BANNERS}/hero-cookie-melt-mobile.jpg",
                "eyebrow": "Straight from the oven",
                "headline": "Cookie melts,",
                "highlight": "still gooey",
                "cta_text": "Shop cookie melts",
                "cta_href": "/cat-cookiemelt",
                "secondary_text": "See the menu",
                "secondary_href": "/all-products",
            },
            {
                "image": f"{BANNERS}/hero-mix-box.jpg",
                "image_mobile": f"{BANNERS}/hero-mix-box-mobile.jpg",
                "eyebrow": "Build your own",
                "headline": "Nine treats.",
                "highlight": "One pink box.",
                "cta_text": "Shop mix boxes",
                "cta_href": "/cat-mixboxes",
            },
            {
                "image": f"{BANNERS}/hero-brownies.jpg",
                "image_mobile": f"{BANNERS}/hero-brownies-mobile.jpg",
                "eyebrow": "Fudgy, never dry",
                "headline": "Brownies worth",
                "highlight": "the detour",
                "cta_text": "Shop brownies",
                "cta_href": "/cat-brownies",
            },
        ],
    },
    "usps": {
        "theme": "plum",
        "speed_s": 38,
        "items": [
            {"icon": "local_shipping", "label": "Delivered across the UAE"},
            {"icon": "schedule", "label": "Same-day slots"},
            {"icon": "favorite", "label": "Baked to order"},
            {"icon": "spa", "label": "No preservatives"},
            {"icon": "card_giftcard", "label": "Gift-ready boxes"},
            {"icon": "place", "label": "Baked in Sharjah"},
        ],
    },
    "featured": {
        "title": "Bestsellers",
        "subtitle": "The ones that go first.",
        "view_all_text": "Shop all",
        "view_all_href": "/all-products",
        "badge_text": "Bestseller",
    },
    "categories": {
        "title": "Shop by craving",
        "subtitle": "Pick your corner of the menu.",
        "count_suffix": "treats",
        "tiles": _tiles(),
    },
    "promos": {
        "items": [
            {
                "image": f"{BANNERS}/banner-cookies.jpg",
                "image_mobile": f"{BANNERS}/banner-cookies-mobile.jpg",
                "eyebrow": "Fresh batch",
                "title": "Soft in the middle. Always.",
                "cta_text": "Shop cookies",
                "cta_href": "/cat-cookies",
            },
            {
                "image": f"{BANNERS}/banner-desserts.jpg",
                "image_mobile": f"{BANNERS}/banner-desserts-mobile.jpg",
                "eyebrow": "Straight from the fridge",
                "title": "Tiramisu, basque, mousse.",
                "cta_text": "Shop desserts",
                "cta_href": "/cat-desserts",
            },
        ]
    },
    "baker": {
        "label": "Meet the baker",
        "quote": "Small batches, the way I'd make them for my own table.",
        "body": "Fatema Abbasi — founder, and the one still doing the baking.",
        "button_text": "Our story",
        "button_href": "/about",
        "image": f"{PHOTOS}/person_shot_1.jpg",
        "gallery": KITCHEN_GALLERY,
    },
    "cater": {
        "title": "We cater to",
        "subtitle": "Boxes for the days that matter.",
        "cta_text": "Plan an order",
        "cta_href": "/contact",
    },
}

HOME_AR: dict[str, Any] = {
    "layout": {"order": SECTION_ORDER, "hidden": []},
    "hero": {
        "autoplay_ms": 6000,
        "slides": [
            {
                "image": f"{BANNERS}/hero-cookie-melt.jpg",
                "image_mobile": f"{BANNERS}/hero-cookie-melt-mobile.jpg",
                "eyebrow": "طازجة من الفرن",
                "headline": "كوكي ملت",
                "highlight": "ما زالت ذائبة",
                "cta_text": "تسوّق كوكي ملت",
                "cta_href": "/cat-cookiemelt",
                "secondary_text": "تصفّح القائمة",
                "secondary_href": "/all-products",
            },
            {
                "image": f"{BANNERS}/hero-mix-box.jpg",
                "image_mobile": f"{BANNERS}/hero-mix-box-mobile.jpg",
                "eyebrow": "اصنع صندوقك",
                "headline": "تسع قطع.",
                "highlight": "صندوق واحد.",
                "cta_text": "تسوّق الصناديق المشكّلة",
                "cta_href": "/cat-mixboxes",
            },
            {
                "image": f"{BANNERS}/hero-brownies.jpg",
                "image_mobile": f"{BANNERS}/hero-brownies-mobile.jpg",
                "eyebrow": "طرية ولا تجف",
                "headline": "براوني تستحق",
                "highlight": "الانعطاف إليها",
                "cta_text": "تسوّق البراوني",
                "cta_href": "/cat-brownies",
            },
        ],
    },
    "usps": {
        "theme": "plum",
        "speed_s": 38,
        "items": [
            {"icon": "local_shipping", "label": "توصيل لكل الإمارات"},
            {"icon": "schedule", "label": "مواعيد في نفس اليوم"},
            {"icon": "favorite", "label": "تُخبز عند الطلب"},
            {"icon": "spa", "label": "بلا مواد حافظة"},
            {"icon": "card_giftcard", "label": "علب جاهزة للإهداء"},
            {"icon": "place", "label": "تُخبز في الشارقة"},
        ],
    },
    "featured": {
        "title": "الأكثر مبيعاً",
        "subtitle": "ما ينفد أولاً.",
        "view_all_text": "تسوّق الكل",
        "view_all_href": "/all-products",
        "badge_text": "الأكثر مبيعاً",
    },
    "categories": {
        "title": "تسوّق حسب مزاجك",
        "subtitle": "اختر ركنك من القائمة.",
        "count_suffix": "صنف",
        "tiles": _tiles(),
    },
    "promos": {
        "items": [
            {
                "image": f"{BANNERS}/banner-cookies.jpg",
                "image_mobile": f"{BANNERS}/banner-cookies-mobile.jpg",
                "eyebrow": "دفعة طازجة",
                "title": "طرية من الداخل. دائماً.",
                "cta_text": "تسوّق الكوكيز",
                "cta_href": "/cat-cookies",
            },
            {
                "image": f"{BANNERS}/banner-desserts.jpg",
                "image_mobile": f"{BANNERS}/banner-desserts-mobile.jpg",
                "eyebrow": "من الثلاجة",
                "title": "تيراميسو، باسك، موس.",
                "cta_text": "تسوّق الحلويات",
                "cta_href": "/cat-desserts",
            },
        ]
    },
    "baker": {
        "label": "تعرّف على الخبّازة",
        "quote": "دفعات صغيرة، كما أُعدّها لمائدتي.",
        "body": "فاطمة عباسي — المؤسِّسة، وما زالت هي من تخبز.",
        "button_text": "قصتنا",
        "button_href": "/about",
        "image": f"{PHOTOS}/person_shot_1.jpg",
        "gallery": KITCHEN_GALLERY,
    },
    "cater": {
        "title": "نلبّي مناسباتك",
        "subtitle": "علب للأيام التي تهمّ.",
        "cta_text": "خطط لطلبك",
        "cta_href": "/contact",
    },
}

DEFAULTS = {"en": HOME_EN, "ar": HOME_AR}


def _merge(defaults: dict, existing: dict) -> dict:
    """Defaults underneath, whatever is already stored on top."""
    merged = dict(defaults)
    for key, value in existing.items():
        base = merged.get(key)
        if isinstance(base, dict) and isinstance(value, dict):
            merged[key] = _merge(base, value)
        else:
            merged[key] = value
    return merged


# Fields the redesign is *about* replacing — keeping the stored value here would
# put the three-sentence bio back under the pull quote it was written out of.
FORCED = [("baker", "body")]


def _force(locale_content: dict, defaults: dict) -> None:
    for section, field in FORCED:
        default = (defaults.get(section) or {}).get(field)
        if default is not None and section in locale_content:
            locale_content[section][field] = default


def upgrade() -> None:
    conn = op.get_bind()
    row = conn.execute(
        sa.text("SELECT content FROM cms_pages WHERE slug = 'home'")
    ).fetchone()
    if row is None:
        return

    content = row[0] if isinstance(row[0], dict) else json.loads(row[0])

    for locale, defaults in DEFAULTS.items():
        locale_content = _merge(defaults, content.get(locale) or {})
        _force(locale_content, defaults)
        # The flat hero has no home in the carousel shape.
        for key in LEGACY_HERO_KEYS:
            locale_content.get("hero", {}).pop(key, None)
        content[locale] = locale_content

    conn.execute(
        sa.text(
            "UPDATE cms_pages SET content = :content, updated_at = NOW() WHERE slug = 'home'"
        ),
        {"content": json.dumps(content)},
    )


def downgrade() -> None:
    """Strip the new blocks and put the flat hero back from slide one."""
    conn = op.get_bind()
    row = conn.execute(
        sa.text("SELECT content FROM cms_pages WHERE slug = 'home'")
    ).fetchone()
    if row is None:
        return

    content = row[0] if isinstance(row[0], dict) else json.loads(row[0])

    for locale in list(content.keys()):
        locale_content = content.get(locale) or {}
        hero = locale_content.get("hero") or {}
        slide = (hero.get("slides") or [{}])[0]
        locale_content["hero"] = {
            "tagline": slide.get("eyebrow", ""),
            "headline": " ".join(
                filter(None, [slide.get("headline"), slide.get("highlight")])
            ),
            "body": slide.get("body", ""),
            "shop_button_text": slide.get("cta_text", ""),
            "shop_button_href": slide.get("cta_href", "/all-products"),
            "story_button_text": slide.get("secondary_text", ""),
            "story_button_href": slide.get("secondary_href", "/about"),
        }
        for key in ("layout", "usps", "categories", "promos"):
            locale_content.pop(key, None)
        for key in ("subtitle", "badge_text"):
            locale_content.get("featured", {}).pop(key, None)
        for key in ("image", "gallery"):
            locale_content.get("baker", {}).pop(key, None)
        for key in ("subtitle", "cta_text", "cta_href"):
            locale_content.get("cater", {}).pop(key, None)
        content[locale] = locale_content

    conn.execute(
        sa.text(
            "UPDATE cms_pages SET content = :content, updated_at = NOW() WHERE slug = 'home'"
        ),
        {"content": json.dumps(content)},
    )
