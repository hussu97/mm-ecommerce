"""Add home page CMS content (EN + AR)

Revision ID: 011_cms_home_page
Revises: 010_cms_privacy_page
Create Date: 2026-03-06 00:00:00.000000

"""

import uuid
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "011_cms_home_page"
down_revision: Union[str, None] = "010_cms_privacy_page"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Content ───────────────────────────────────────────────────────────────────

HOME_EN = {
    "hero": {
        "tagline": "Handcrafted in the UAE",
        "headline": "Moments that melt away",
        "body": (
            "Artisanal brownies, cookies, and desserts — lovingly baked by Fatema Abbasi. "
            "Every bite is a little piece of home."
        ),
        "shop_button_text": "Shop Now",
        "shop_button_href": "/all-products",
        "story_button_text": "Our Story",
        "story_button_href": "/about",
    },
    "featured": {
        "title": "Our Bestsellers",
        "view_all_text": "View All",
        "view_all_href": "/all-products",
    },
    "baker": {
        "label": "Meet the Baker",
        "quote": "Every treat is baked with intention and love.",
        "body": (
            "Hi, I\u2019m Fatema Abbasi \u2014 a self-taught baker based in the UAE. "
            "Melting Moments started as a passion project to bring comfort through food, "
            "one handcrafted dessert at a time."
        ),
        "button_text": "Read More",
        "button_href": "/about",
    },
    "cater": {
        "title": "We Cater To",
        "occasions": [
            {"icon": "\U0001f382", "label": "Birthdays"},
            {"icon": "\U0001f48d", "label": "Weddings"},
            {"icon": "\U0001f4bc", "label": "Corporate"},
            {"icon": "\U0001f319", "label": "Eid"},
            {"icon": "\u2728", "label": "Ramadan"},
            {"icon": "\U0001f389", "label": "Celebrations"},
        ],
    },
    "seo": {
        "title": "Melting Moments Cakes \u2014 Artisanal Bakery in UAE",
        "description": (
            "Handcrafted brownies, cookies, cookie melts, and desserts delivered across the UAE. "
            "Made with 100% love by Fatema Abbasi."
        ),
    },
}

HOME_AR = {
    "hero": {
        "tagline": "\u0645\u0635\u0646\u0648\u0639\u0629 \u064a\u062f\u0648\u064a\u0627\u064b \u0641\u064a \u0627\u0644\u0625\u0645\u0627\u0631\u0627\u062a",
        "headline": "\u0644\u062d\u0638\u0627\u062a \u062a\u0630\u0648\u0628 \u0641\u064a \u0627\u0644\u0641\u0645",
        "body": (
            "\u0628\u0631\u0627\u0648\u0646\u064a \u0648\u0643\u0648\u0643\u064a\u0632 \u0648\u062d\u0644\u0648\u064a\u0627\u062a \u062d\u0631\u0641\u064a\u0629 \u2014 "
            "\u062a\u064f\u062e\u0628\u0632\u0647\u0627 \u0641\u0627\u0637\u0645\u0629 \u0639\u0628\u0627\u0633\u064a \u0628\u0643\u0644 \u062d\u0628. "
            "\u0643\u0644 \u0642\u0636\u0645\u0629 \u0642\u0637\u0639\u0629 \u0635\u063a\u064a\u0631\u0629 \u0645\u0646 \u0627\u0644\u062f\u0641\u0621."
        ),
        "shop_button_text": "\u062a\u0633\u0648\u0651\u0642 \u0627\u0644\u0622\u0646",
        "shop_button_href": "/all-products",
        "story_button_text": "\u0642\u0635\u062a\u0646\u0627",
        "story_button_href": "/about",
    },
    "featured": {
        "title": "\u0627\u0644\u0623\u0643\u062b\u0631 \u0645\u0628\u064a\u0639\u0627\u064b",
        "view_all_text": "\u0639\u0631\u0636 \u0627\u0644\u0643\u0644",
        "view_all_href": "/all-products",
    },
    "baker": {
        "label": "\u062a\u0639\u0631\u0651\u0641 \u0639\u0644\u0649 \u0627\u0644\u062e\u0628\u0651\u0627\u0632\u0629",
        "quote": "\u0643\u0644 \u062d\u0644\u0648\u0649 \u062a\u064f\u062e\u0628\u0632 \u0628\u0646\u064a\u0629 \u0648\u062d\u0628.",
        "body": (
            "\u0645\u0631\u062d\u0628\u0627\u064b\u060c \u0623\u0646\u0627 \u0641\u0627\u0637\u0645\u0629 \u0639\u0628\u0627\u0633\u064a \u2014 "
            "\u062e\u0628\u0651\u0627\u0632\u0629 \u0645\u062a\u0639\u0644\u0651\u0645\u0629 \u0630\u0627\u062a\u064a\u0627\u064b \u0645\u0642\u064a\u0645\u0629 \u0641\u064a \u0627\u0644\u0625\u0645\u0627\u0631\u0627\u062a. "
            "\u0628\u062f\u0623\u062a \u0645\u0644\u062a\u064a\u0646\u062c \u0645\u0648\u0645\u0646\u062a\u0633 \u0643\u0645\u0634\u0631\u0648\u0639 \u0634\u063a\u0641 \u0644\u0625\u062f\u062e\u0627\u0644 \u0627\u0644\u062f\u0641\u0621 \u0648\u0627\u0644\u0628\u0647\u062c\u0629 \u0645\u0646 \u062e\u0644\u0627\u0644 \u0627\u0644\u0637\u0639\u0627\u0645\u060c "
            "\u062d\u0644\u0648\u0649 \u064a\u062f\u0648\u064a\u0629 \u0641\u064a \u0643\u0644 \u0645\u0631\u0629."
        ),
        "button_text": "\u0627\u0642\u0631\u0623 \u0623\u0643\u062b\u0631",
        "button_href": "/about",
    },
    "cater": {
        "title": "\u0646\u0644\u0628\u0651\u064a \u0645\u0646\u0627\u0633\u0628\u0627\u062a\u0643",
        "occasions": [
            {
                "icon": "\U0001f382",
                "label": "\u0623\u0639\u064a\u0627\u062f \u0627\u0644\u0645\u064a\u0644\u0627\u062f",
            },
            {
                "icon": "\U0001f48d",
                "label": "\u0627\u0644\u0623\u0639\u0631\u0627\u0633",
            },
            {"icon": "\U0001f4bc", "label": "\u0645\u0624\u0633\u0633\u0627\u062a"},
            {"icon": "\U0001f319", "label": "\u0639\u064a\u062f"},
            {"icon": "\u2728", "label": "\u0631\u0645\u0636\u0627\u0646"},
            {
                "icon": "\U0001f389",
                "label": "\u0627\u062d\u062a\u0641\u0627\u0644\u0627\u062a",
            },
        ],
    },
    "seo": {
        "title": "\u0645\u0644\u062a\u064a\u0646\u062c \u0645\u0648\u0645\u0646\u062a\u0633 \u2014 \u0645\u062e\u0628\u0632 \u062d\u0631\u0641\u064a \u0641\u064a \u0627\u0644\u0625\u0645\u0627\u0631\u0627\u062a",
        "description": (
            "\u0628\u0631\u0627\u0648\u0646\u064a \u0648\u0643\u0648\u0643\u064a\u0632 \u0648\u0643\u0648\u0643\u064a \u0645\u0644\u062a\u0633 \u0648\u062d\u0644\u0648\u064a\u0627\u062a \u062d\u0631\u0641\u064a\u0629 \u064a\u062f\u0648\u064a\u0629 \u0627\u0644\u0635\u0646\u0639\u060c "
            "\u062a\u064f\u0648\u0635\u064e\u0651\u0644 \u0641\u064a \u0623\u0646\u062d\u0627\u0621 \u0627\u0644\u0625\u0645\u0627\u0631\u0627\u062a. \u0645\u0635\u0646\u0648\u0639\u0629 \u0628\u0640 100\u066a \u062d\u0628 \u0639\u0644\u0649 \u064a\u062f \u0641\u0627\u0637\u0645\u0629 \u0639\u0628\u0627\u0633\u064a."
        ),
    },
}


def upgrade() -> None:
    cms_pages = sa.table(
        "cms_pages",
        sa.column("id", UUID),
        sa.column("slug", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("content", JSONB),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )

    now = datetime.utcnow()
    op.bulk_insert(
        cms_pages,
        [
            {
                "id": uuid.uuid4(),
                "slug": "home",
                "is_active": True,
                "content": {"en": HOME_EN, "ar": HOME_AR},
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM cms_pages WHERE slug = 'home'")
