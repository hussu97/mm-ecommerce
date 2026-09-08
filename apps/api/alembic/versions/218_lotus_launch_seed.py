"""Lotus Cookie Melt launch: lead hero slide + merchandising flags.

The launch content the deploy has to carry (CLAUDE.md rule 7): a hero slide at
the top of the home carousel pointing at the Lotus PDP, and the flags that thread
the product through the bestseller rail, the empty-cart carousel and the cart
add-on tray. Content, not schema, but it belongs in a migration for the same
reason every CMS rewrite here does — a script only lands if someone remembers to
run it.

Everything here is **guarded so it cannot fight the admin**, and safe on a
restored dump:

  * It keys entirely off the product row `slug = 'lotus-cookie-melt'`. If that
    product does not exist yet, the whole migration is a no-op — the launch
    sequence is: create the product (admin) → add banner art → deploy this.
  * The `cta_href` is *derived* from the product's own category + slug, so the
    link is correct without hardcoding which category Lotus lives in.
  * The slide is inserted only if no slide in that locale already points at that
    href — so once a human edits or removes it in the console, this matches
    nothing.
  * `is_featured` / `is_cart_addon` are set only while still at their default
    off, and `display_order` only while still the default 0, so a later admin
    decision is never stomped. The label is prepended only if absent.

Revision ID: 218_lotus_launch_seed
Revises: 217_product_labels
Create Date: 2026-09-08
"""

from __future__ import annotations

import json
from typing import Any, Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "218_lotus_launch_seed"
down_revision: Union[str, None] = "217_product_labels"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The Lotus Cookie Melt ships in two sizes. "The big one" — the 500g — is the
# flagship the hero leads to and the one that rides the bestseller rail and cart
# carousel. Both sizes wear the launch's "Website Exclusive" badge.
FLAGSHIP_SLUG = "lotus-cookie-melt-500-grams"
EXCLUSIVE_SLUGS = ("lotus-cookie-melt-500-grams", "lotus-cookie-melt-250-grams")
BANNERS = "/images/banners"

# The lead slide, per locale. `cta_href` is filled at run time from the product's
# real category so the link resolves regardless of which category holds Lotus.
SLIDE_EN: dict[str, Any] = {
    "image": f"{BANNERS}/hero-lotus-cookie-melt.jpg",
    "image_mobile": f"{BANNERS}/hero-lotus-cookie-melt-mobile.jpg",
    "image_alt": "Lotus Cookie Melt — Biscoff-topped and gooey",
    "eyebrow": "New — only on our website",
    "headline": "Lotus Cookie Melt,",
    "highlight": "just landed",
    "cta_text": "Shop Lotus Cookie Melt",
    "secondary_text": "See the menu",
    "secondary_href": "/all-products",
}
SLIDE_AR: dict[str, Any] = {
    "image": f"{BANNERS}/hero-lotus-cookie-melt.jpg",
    "image_mobile": f"{BANNERS}/hero-lotus-cookie-melt-mobile.jpg",
    "image_alt": "لوتس كوكي ملت — بطبقة بسكوف وذائبة",
    "eyebrow": "جديد — حصري على موقعنا",
    "headline": "لوتس كوكي ملت،",
    "highlight": "وصلت للتو",
    "cta_text": "تسوّق لوتس كوكي ملت",
    "secondary_text": "تصفّح القائمة",
    "secondary_href": "/all-products",
}
SLIDES = {"en": SLIDE_EN, "ar": SLIDE_AR}


def _pdp_href(conn) -> str | None:
    """`/<category-slug>/<flagship-slug>`, or None if the 500g isn't there."""
    row = conn.execute(
        sa.text(
            """
            SELECT c.slug
            FROM products p
            JOIN categories c ON c.id = p.category_id
            WHERE p.slug = :slug
            """
        ),
        {"slug": FLAGSHIP_SLUG},
    ).fetchone()
    if row is None or not row[0]:
        return None
    return f"/{row[0]}/{FLAGSHIP_SLUG}"


def upgrade() -> None:
    conn = op.get_bind()

    href = _pdp_href(conn)
    if href is None:
        # No Lotus product (or it has no category) — nothing to seed yet.
        return

    # ── Merchandising, each part guarded so it never overrides an admin edit ──
    # Both sizes wear website_exclusive. Rebuilt in canonical priority order and
    # idempotent (a second run adds nothing), keeping any label an admin already
    # set.
    conn.execute(
        sa.text(
            """
            UPDATE products SET
                labels = ARRAY(
                    SELECT l
                    FROM unnest(
                        ARRAY['website_exclusive','bestseller','new','limited']::varchar[]
                    ) AS l
                    WHERE l = ANY(labels) OR l = 'website_exclusive'
                ),
                updated_at = NOW()
            WHERE slug = ANY(:slugs)
            """
        ),
        {"slugs": list(EXCLUSIVE_SLUGS)},
    )

    # The flagship additionally rides the bestseller rail + cart tray and leads
    # its category. bestseller is added on top of website_exclusive; cart-addon
    # and display_order are only nudged while still at their defaults.
    conn.execute(
        sa.text(
            """
            UPDATE products SET
                is_cart_addon = CASE WHEN is_cart_addon THEN is_cart_addon ELSE true END,
                display_order = CASE WHEN display_order = 0 THEN -1 ELSE display_order END,
                labels = ARRAY(
                    SELECT l
                    FROM unnest(
                        ARRAY['website_exclusive','bestseller','new','limited']::varchar[]
                    ) AS l
                    WHERE l = ANY(labels) OR l IN ('website_exclusive','bestseller')
                ),
                updated_at = NOW()
            WHERE slug = :slug
            """
        ),
        {"slug": FLAGSHIP_SLUG},
    )

    # ── Lead hero slide, inserted at index 0 if not already present ────────────
    row = conn.execute(
        sa.text("SELECT content FROM cms_pages WHERE slug = 'home'")
    ).fetchone()
    if row is None:
        return
    content = row[0] if isinstance(row[0], dict) else json.loads(row[0])

    changed = False
    for locale, slide in SLIDES.items():
        locale_content = content.get(locale)
        if not isinstance(locale_content, dict):
            continue
        hero = locale_content.setdefault("hero", {})
        slides = hero.setdefault("slides", [])
        if any(isinstance(s, dict) and s.get("cta_href") == href for s in slides):
            continue  # a slide already points at Lotus — leave the console's copy
        slides.insert(0, {**slide, "cta_href": href})
        changed = True

    if changed:
        conn.execute(
            sa.text(
                "UPDATE cms_pages SET content = :content, updated_at = NOW() "
                "WHERE slug = 'home'"
            ),
            {"content": json.dumps(content)},
        )


def downgrade() -> None:
    """Remove the Lotus lead slide and back out the website_exclusive label.

    Leaves `bestseller` / `is_cart_addon` / `display_order` as they are: by the
    time anyone downgrades, those may reflect deliberate merchandising, and this
    migration cannot tell its own write apart from a later human one.
    """
    conn = op.get_bind()

    href = _pdp_href(conn)
    conn.execute(
        sa.text(
            "UPDATE products SET labels = array_remove(labels, 'website_exclusive'), "
            "updated_at = NOW() WHERE slug = ANY(:slugs)"
        ),
        {"slugs": list(EXCLUSIVE_SLUGS)},
    )

    if href is None:
        return
    row = conn.execute(
        sa.text("SELECT content FROM cms_pages WHERE slug = 'home'")
    ).fetchone()
    if row is None:
        return
    content = row[0] if isinstance(row[0], dict) else json.loads(row[0])

    changed = False
    for locale in list(content.keys()):
        locale_content = content.get(locale) or {}
        hero = locale_content.get("hero") or {}
        slides = hero.get("slides")
        if not isinstance(slides, list):
            continue
        kept = [
            s for s in slides if not (isinstance(s, dict) and s.get("cta_href") == href)
        ]
        if len(kept) != len(slides):
            hero["slides"] = kept
            changed = True

    if changed:
        conn.execute(
            sa.text(
                "UPDATE cms_pages SET content = :content, updated_at = NOW() "
                "WHERE slug = 'home'"
            ),
            {"content": json.dumps(content)},
        )
