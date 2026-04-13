"""Add missing label/UI text fields to about and contact CMS pages

Previously hardcoded strings in the frontend components:
  About:   "Our Story", "Get in Touch", "Our Promise", "What we stand for"
  Contact: "Reach Out", info card titles/CTAs, "Follow Along", Instagram handle/URL

These are now stored in CMS so they can be translated via the admin content editor.

Revision ID: 028
Revises: 027
Create Date: 2026-04-14
"""

from typing import Sequence, Union

import json

from alembic import op
import sqlalchemy as sa

revision: str = "028_cms_about_contact_labels"
down_revision: Union[str, None] = "027_regions_delivery_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ── About additions ────────────────────────────────────────────────────────────
# hero.label, story_2.cta_text, values_section {label, title}

ABOUT_ADDITIONS = {
    "en": {
        "hero": {"label": "Our Story"},
        "story_2": {"cta_text": "Get in Touch"},
        "values_section": {"label": "Our Promise", "title": "What we stand for"},
    },
    "ar": {
        "hero": {"label": "قصتنا"},
        "story_2": {"cta_text": "تواصل معنا"},
        "values_section": {"label": "وعدنا", "title": "ما نؤمن به"},
    },
}

# ── Contact additions ──────────────────────────────────────────────────────────
# header.label, cards {titles, ctas}, social {label, instagram}

CONTACT_ADDITIONS = {
    "en": {
        "header": {"label": "Reach Out"},
        "cards": {
            "whatsapp_title": "WhatsApp",
            "whatsapp_cta": "Message us",
            "email_title": "Email",
            "email_cta": "Send email",
            "location_title": "Location",
            "hours_title": "Hours",
        },
        "social": {
            "label": "Follow Along",
            "instagram_label": "Instagram",
            "instagram_handle": "@meltingmomentscakes",
            "instagram_url": "https://www.instagram.com/meltingmomentscakes",
        },
    },
    "ar": {
        "header": {"label": "تواصل بنا"},
        "cards": {
            "whatsapp_title": "واتساب",
            "whatsapp_cta": "راسلنا",
            "email_title": "البريد الإلكتروني",
            "email_cta": "أرسل بريداً",
            "location_title": "الموقع",
            "hours_title": "أوقات العمل",
        },
        "social": {
            "label": "تابعنا",
            "instagram_label": "Instagram",
            "instagram_handle": "@meltingmomentscakes",
            "instagram_url": "https://www.instagram.com/meltingmomentscakes",
        },
    },
}


def _deep_merge(base: dict, additions: dict) -> dict:
    """Recursively merge additions into base (non-destructive)."""
    result = dict(base)
    for key, value in additions.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def upgrade() -> None:
    conn = op.get_bind()

    # ── about ──────────────────────────────────────────────────────────────────
    row = conn.execute(
        sa.text("SELECT content FROM cms_pages WHERE slug = 'about'")
    ).fetchone()

    if row:
        content: dict = row[0] or {}
        for locale, additions in ABOUT_ADDITIONS.items():
            locale_content = content.get(locale, {})
            content[locale] = _deep_merge(locale_content, additions)

        conn.execute(
            sa.text(
                "UPDATE cms_pages SET content = CAST(:c AS jsonb), updated_at = now() WHERE slug = 'about'"
            ),
            {"c": json.dumps(content)},
        )

    # ── contact ────────────────────────────────────────────────────────────────
    row = conn.execute(
        sa.text("SELECT content FROM cms_pages WHERE slug = 'contact'")
    ).fetchone()

    if row:
        content = row[0] or {}
        for locale, additions in CONTACT_ADDITIONS.items():
            locale_content = content.get(locale, {})
            content[locale] = _deep_merge(locale_content, additions)

        conn.execute(
            sa.text(
                "UPDATE cms_pages SET content = CAST(:c AS jsonb), updated_at = now() WHERE slug = 'contact'"
            ),
            {"c": json.dumps(content)},
        )


def downgrade() -> None:
    # Remove only the newly-added keys (non-destructive downgrade)
    conn = op.get_bind()

    for slug, removals_by_locale in [
        ("about", ABOUT_ADDITIONS),
        ("contact", CONTACT_ADDITIONS),
    ]:
        row = conn.execute(
            sa.text(f"SELECT content FROM cms_pages WHERE slug = '{slug}'")
        ).fetchone()
        if not row:
            continue
        content: dict = row[0] or {}
        for locale, removals in removals_by_locale.items():
            if locale not in content:
                continue
            for section, keys in removals.items():
                if section in content[locale]:
                    for key in keys:
                        content[locale][section].pop(key, None)
        conn.execute(
            sa.text(
                f"UPDATE cms_pages SET content = CAST(:c AS jsonb), updated_at = now() WHERE slug = '{slug}'"
            ),
            {"c": json.dumps(content)},
        )
