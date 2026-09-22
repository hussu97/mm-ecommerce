"""Relabel the enquiry-form weight field to "(kg) / servings" and drop "optional".

The custom-order enquiry form's approximate-weight field is now mandatory (a
lead we cannot size is a lead we cannot quote), and it accepts either a weight
in kg or a number of servings — whichever the customer thinks in. The label,
seeded as CMS content by 263, has to move with the deploy.

Guarded per the content-migration rule: it swaps `kg_label` only where it still
holds the exact 263 default, so once a human edits it in the Content console (or
on a restored dump) this matches nothing and does nothing.

Revision ID: 277_cater_kg_label_servings
Revises: 276_po_misc_items
Create Date: 2026-09-22
"""

from __future__ import annotations

import json
from typing import Any, Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "277_cater_kg_label_servings"
down_revision: Union[str, None] = "276_po_misc_items"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The exact strings 263 seeded, keyed by locale, and their replacements.
_OLD_KG_LABEL = {
    "en": "Approx. weight (kg) — optional",
    "ar": "الوزن التقريبي (كجم) — اختياري",
}
_NEW_KG_LABEL = {
    "en": "Approx. weight (kg) / servings",
    "ar": "الوزن التقريبي (كجم) / عدد الحصص",
}


def _swap(old: dict[str, str], new: dict[str, str]) -> None:
    conn = op.get_bind()
    row = conn.execute(
        sa.text("SELECT content FROM cms_pages WHERE slug = 'home'")
    ).fetchone()
    if row is None:
        return
    content: dict[str, Any] = row[0] if isinstance(row[0], dict) else json.loads(row[0])

    changed = False
    for locale in old:
        form = ((content.get(locale) or {}).get("cater") or {}).get("form")
        if isinstance(form, dict) and form.get("kg_label") == old[locale]:
            form["kg_label"] = new[locale]
            changed = True

    if changed:
        conn.execute(
            sa.text(
                "UPDATE cms_pages SET content = :content, updated_at = NOW() "
                "WHERE slug = 'home'"
            ),
            {"content": json.dumps(content)},
        )


def upgrade() -> None:
    _swap(_OLD_KG_LABEL, _NEW_KG_LABEL)


def downgrade() -> None:
    _swap(_NEW_KG_LABEL, _OLD_KG_LABEL)
