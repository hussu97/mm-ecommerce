"""Molten Cookie Single out of the counter's 15% off.

"Counter 15% Off" is scoped by category (`promotion_rules.per_line_targets`):
a counter line takes the discount when its product's category is one of the
promotion's seven, and Cookies is one of them. The engine has no per-product
exclusion — and the register prices counter sales on the iPad from the same
rules (`counter_pricing`, ported to Swift), so adding one is an app release.

So the cookie gets a category of its own, which the promotion does not list.
Nothing a customer sees moves: the product is counter-only (no `web` channel,
so no storefront page, and the website hides a category with nothing sold
online), the register lays out its grid from menu groups rather than
categories, no kitchen flow routes on categories, and the marketplace sync
groups by the integrator menu. What moves is the discount, and the line its
sales report under.

Guarded so it cannot fight the admin: the category is created only if no
category already has its slug, and the product is moved only from Cookies —
once anybody files it elsewhere by hand, this matches nothing. The SKU is the
key rather than the id, which differs per environment; where FG0140 or the
Cookies category does not exist the move is a no-op.

Revision ID: 316_molten_cookie_own_category
Revises: 315_branch_busy_mode
Create Date: 2026-10-31
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "316_molten_cookie_own_category"
down_revision: Union[str, None] = "315_branch_busy_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SLUG = "molten-cookies"
SKU = "FG0140"
FROM_SLUG = "cookies"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO categories
                (id, name, slug, description, display_order, is_active,
                 reference, translations, created_at, updated_at)
            SELECT gen_random_uuid(), 'Molten Cookies', :slug,
                   'A warm molten cookie, baked and served at our counter.',
                   c.display_order, true, 'cat_moltencookies',
                   jsonb_build_object('ar', jsonb_build_object(
                       'name', 'كوكيز مولتن',
                       'description', 'كوكيز مولتن دافئ يُخبز ويُقدَّم في فروعنا.')),
                   now(), now()
              FROM categories c
             WHERE c.slug = :from_slug
               AND NOT EXISTS (SELECT 1 FROM categories WHERE slug = :slug)
               AND NOT EXISTS (
                   SELECT 1 FROM categories WHERE reference = 'cat_moltencookies')
            """
        ).bindparams(slug=SLUG, from_slug=FROM_SLUG)
    )
    op.execute(
        sa.text(
            """
            UPDATE products p
               SET category_id = (SELECT id FROM categories WHERE slug = :slug),
                   updated_at = now()
             WHERE p.sku = :sku
               AND p.category_id = (SELECT id FROM categories WHERE slug = :from_slug)
               AND EXISTS (SELECT 1 FROM categories WHERE slug = :slug)
            """
        ).bindparams(slug=SLUG, sku=SKU, from_slug=FROM_SLUG)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE products p
               SET category_id = (SELECT id FROM categories WHERE slug = :from_slug),
                   updated_at = now()
             WHERE p.sku = :sku
               AND p.category_id = (SELECT id FROM categories WHERE slug = :slug)
            """
        ).bindparams(slug=SLUG, sku=SKU, from_slug=FROM_SLUG)
    )
    op.execute(
        sa.text(
            """
            DELETE FROM categories c
             WHERE c.slug = :slug
               AND NOT EXISTS (SELECT 1 FROM products WHERE category_id = c.id)
            """
        ).bindparams(slug=SLUG)
    )
