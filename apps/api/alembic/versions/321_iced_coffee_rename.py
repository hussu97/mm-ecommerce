"""Cold Coffee is now called Iced Coffee — everywhere it is named.

The shop renamed the category. One name across the catalogue: the product
category (name, slug, reference, Arabic), every menu-tree group that carried it
(the branch counters and the integrator category the marketplaces read), in
English and Arabic. The Foodics side follows: the catalog sync renames an
integrator category's Grubtech subgroup to match (`_foodics_marketplace_parity`),
and the Foodics POS category/group were renamed alongside this release.

Arabic: "القهوة المثلجة" / "قهوة مثلجة" (iced), replacing "القهوة الباردة" /
"قهوة باردة" (cold) — the same word the drinks themselves already use
("لاتيه إسباني مثلج").

Guarded so it cannot fight the admin: every update matches the exact old value,
so once anybody renames it again by hand this matches nothing, and the slug and
reference move only when the new ones are free. No storefront redirect is
needed: no drink in the category sells on the website, which hides a category
with nothing sold online.

Revision ID: 321_iced_coffee_rename
Revises: 320_coffee_on_marketplaces
Create Date: 2026-11-05
"""

from __future__ import annotations

import re
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "321_iced_coffee_rename"
down_revision: Union[str, None] = "320_coffee_on_marketplaces"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD, NEW = "Cold Coffee", "Iced Coffee"
OLD_AR_LONG, NEW_AR_LONG = "القهوة الباردة", "القهوة المثلجة"
OLD_AR, NEW_AR = "قهوة باردة", "قهوة مثلجة"


def _bound(sql: str, params: dict) -> sa.TextClause:
    """`text().bindparams` refuses a parameter the statement does not use, so
    hand each statement only its own."""
    used = set(re.findall(r":([a-z_]+)", sql))
    return sa.text(sql).bindparams(**{k: v for k, v in params.items() if k in used})


def _rename(
    old,
    new,
    old_ar_long,
    new_ar_long,
    old_ar,
    new_ar,
    old_slug,
    new_slug,
    old_ref,
    new_ref,
) -> None:
    params = dict(
        old=old,
        new=new,
        old_ar_long=old_ar_long,
        new_ar_long=new_ar_long,
        old_ar=old_ar,
        new_ar=new_ar,
        old_slug=old_slug,
        new_slug=new_slug,
        old_ref=old_ref,
        new_ref=new_ref,
    )
    op.execute(
        _bound(
            """
            UPDATE categories
               SET name = CAST(:new AS varchar),
                   slug = CASE WHEN slug = CAST(:old_slug AS varchar)
                                AND NOT EXISTS (SELECT 1 FROM categories c2
                                                 WHERE c2.slug = CAST(:new_slug AS varchar))
                               THEN CAST(:new_slug AS varchar) ELSE slug END,
                   reference = CASE WHEN reference = CAST(:old_ref AS varchar)
                                     AND NOT EXISTS (SELECT 1 FROM categories c3
                                                      WHERE c3.reference = CAST(:new_ref AS varchar))
                                    THEN CAST(:new_ref AS varchar) ELSE reference END,
                   translations = CASE
                       WHEN translations #>> '{ar,name}' IN (CAST(:old_ar_long AS text), CAST(:old_ar AS text))
                       THEN jsonb_set(translations, '{ar,name}', to_jsonb(CAST(:new_ar_long AS text)))
                       ELSE translations END,
                   updated_at = now()
             WHERE name = CAST(:old AS varchar)
            """,
            params,
        )
    )
    op.execute(
        _bound(
            """
            UPDATE menu_groups
               SET name = CAST(:new AS varchar),
                   name_localized = CASE name_localized
                       WHEN CAST(:old_ar_long AS varchar) THEN CAST(:new_ar_long AS varchar)
                       WHEN CAST(:old_ar AS varchar) THEN CAST(:new_ar AS varchar)
                       ELSE name_localized END,
                   translations = CASE translations #>> '{ar,name}'
                       WHEN CAST(:old_ar_long AS text)
                       THEN jsonb_set(translations, '{ar,name}', to_jsonb(CAST(:new_ar_long AS text)))
                       WHEN CAST(:old_ar AS text)
                       THEN jsonb_set(translations, '{ar,name}', to_jsonb(CAST(:new_ar AS text)))
                       ELSE translations END,
                   updated_at = now()
             WHERE name = CAST(:old AS varchar) AND deleted_at IS NULL
            """,
            params,
        )
    )


def upgrade() -> None:
    _rename(
        OLD,
        NEW,
        OLD_AR_LONG,
        NEW_AR_LONG,
        OLD_AR,
        NEW_AR,
        "cat-cold-coffee",
        "cat-iced-coffee",
        "cat_cold_coffee",
        "cat_iced_coffee",
    )


def downgrade() -> None:
    _rename(
        NEW,
        OLD,
        NEW_AR_LONG,
        OLD_AR_LONG,
        NEW_AR,
        OLD_AR,
        "cat-iced-coffee",
        "cat-cold-coffee",
        "cat_iced_coffee",
        "cat_cold_coffee",
    )
